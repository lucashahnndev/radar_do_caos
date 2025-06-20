from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta
import sqlite3
import secrets
import os
import yfinance as yf
import json
from typing import Optional, List
from threading import Thread

import bot  # <- importa seu bot.py como módulo

dominio = os.environ.get("dominio")
# Configurações
DB_PATH = "acoes.db"  # Caminho para o banco do bot
SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 horas

app = FastAPI(title="Dashboard de Ações", version="2.0.0")

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Em produção, especificar domínios
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Montar arquivos estáticos do frontend
app.mount("/static", StaticFiles(directory="../frontend"), name="static")

# Configuração de segurança
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# --- Modelos Pydantic ---
class User(BaseModel):
    user_id: int
    username: str | None = None
    theme: str = "dark"

class UserInDB(User):
    hashed_dashboard_key: str

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    user_id: int | None = None

class DashboardKeyUpdate(BaseModel):
    old_key: str
    new_key: str

# Novos modelos para as funcionalidades
class AcaoMonitorada(BaseModel):
    ticker: str
    preco_referencia: float
    preco_atual: Optional[float] = None
    variacao_percentual: Optional[float] = None

class AcaoMonitoradaUpdate(BaseModel):
    preco_referencia: float

class AcaoMonitoradaCreate(BaseModel):
    ticker: str
    preco_referencia: Optional[float] = None

class AlertaPreco(BaseModel):
    ticker: str
    preco_alvo: float
    sentido: str
    notificado: bool = False

class AlertaPrecoCreate(BaseModel):
    ticker: str
    preco_alvo: float

class AlertaPrecoUpdate(BaseModel):
    preco_alvo: float

class AlertaPanico(BaseModel):
    ticker: str
    ativo: bool
    percentual_queda: float

class AlertaPanicoCreate(BaseModel):
    ticker: str
    percentual_queda: float

class AlertaPanicoUpdate(BaseModel):
    ativo: bool
    percentual_queda: float

class AlertaHistorico(BaseModel):
    id: int
    ticker: str
    alert_type: str
    trigger_value: float
    triggered_at: str
    message: str

class ConfiguracaoBot(BaseModel):
    resumo_automatico: bool
    horario_resumo: str
    horario_panico: str

class PortfolioPosition(BaseModel):
    ticker: str
    quantity: float
    avg_price: float
    current_price: float | None = None
    total_value: float | None = None
    profit_loss: float | None = None

# --- Cache simples para dados do yfinance ---
cache_data = {}
CACHE_EXPIRE_MINUTES = 5

def get_cached_data(key: str):
    if key in cache_data:
        data, timestamp = cache_data[key]
        if datetime.now() - timestamp < timedelta(minutes=CACHE_EXPIRE_MINUTES):
            return data
    return None

def set_cached_data(key: str, data):
    cache_data[key] = (data, datetime.now())

# --- Funções de Banco de Dados ---
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_user_from_db(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dashboard_users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    if user_data:
        return UserInDB(
            user_id=user_data["user_id"],
            username=user_data["username"],
            theme=user_data["theme"],
            hashed_dashboard_key=user_data["dashboard_key"]
        )
    return None

def authenticate_user(user_id: int, dashboard_key: str):
    user = get_user_from_db(user_id)
    if not user:
        return False
    if not verify_password(dashboard_key, user.hashed_dashboard_key):
        return False
    return user

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = int(payload.get("sub"))
        if user_id is None:
            raise credentials_exception
        token_data = TokenData(user_id=user_id)
    except JWTError:
        raise credentials_exception
    user = get_user_from_db(user_id=token_data.user_id)
    if user is None:
        raise credentials_exception
    return user

def create_dashboard_user(user_id: int, username: str | None = None):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verificar se o usuário já existe
    cursor.execute("SELECT user_id FROM dashboard_users WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        conn.close()
        return None  # Usuário já existe

    # Gerar chave segura
    dashboard_key = secrets.token_urlsafe(12)  # 16 caracteres
    hashed_dashboard_key = get_password_hash(dashboard_key)

    try:
        cursor.execute(
            "INSERT INTO dashboard_users (user_id, dashboard_key, username, theme) VALUES (?, ?, ?, ?)",
            (user_id, hashed_dashboard_key, username, "dark")
        )
        conn.commit()
        return dashboard_key
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

# --- Funções para dados de ações ---
def get_stock_data(ticker: str, period: str = "1d"):
    cache_key = f"{ticker}_{period}"
    cached = get_cached_data(cache_key)
    if cached:
        return cached

    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if hist.empty:
            return None

        data = {
            "ticker": ticker,
            "history": hist.to_dict('records'),
            "current_price": float(hist["Close"].iloc[-1]),
            "info": stock.info
        }
        set_cached_data(cache_key, data)
        return data
    except Exception as e:
        print(f"Erro ao buscar dados para {ticker}: {e}")
        return None

# --- Funções para as novas funcionalidades ---
def get_acoes_monitoradas_detalhadas(user_id: int):
    """Obter ações monitoradas com preço atual e de referência"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ticker, preco_referencia FROM acoes_monitoradas WHERE user_id = ?", (user_id,))
    acoes = cursor.fetchall()
    conn.close()

    result = []
    for acao in acoes:
        ticker = acao["ticker"]
        preco_referencia = acao["preco_referencia"]

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                preco_atual = float(hist["Close"].iloc[-1])
                variacao_percentual = ((preco_atual - preco_referencia) / preco_referencia) * 100
            else:
                preco_atual = None
                variacao_percentual = None
        except Exception as e:
            print(f"Erro ao obter dados para {ticker}: {e}")
            preco_atual = None
            variacao_percentual = None

        result.append(AcaoMonitorada(
            ticker=ticker,
            preco_referencia=preco_referencia,
            preco_atual=preco_atual,
            variacao_percentual=variacao_percentual
        ))

    return result

def update_acao_monitorada(user_id: int, ticker: str, novo_preco_referencia: float):
    """Atualizar preço de referência de uma ação monitorada"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE acoes_monitoradas SET preco_referencia = ? WHERE user_id = ? AND ticker = ?",
        (novo_preco_referencia, user_id, ticker)
    )
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return affected_rows > 0

def delete_acao_monitorada(user_id: int, ticker: str):
    """Remover ação monitorada"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM acoes_monitoradas WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return affected_rows > 0

def create_acao_monitorada(user_id: int, ticker: str, preco_referencia: Optional[float] = None):
    """Adicionar nova ação monitorada"""
    if preco_referencia is None:
        # Obter preço atual como referência
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                preco_referencia = float(hist["Close"].iloc[-1])
            else:
                return False
        except:
            return False

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO acoes_monitoradas (user_id, ticker, preco_referencia) VALUES (?, ?, ?)",
            (user_id, ticker, preco_referencia)
        )
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def get_alertas_preco(user_id: int):
    """Obter alertas de preço do usuário"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alertas_precos WHERE user_id = ?", (user_id,))
    alertas = cursor.fetchall()
    conn.close()

    return [AlertaPreco(
        ticker=alerta["ticker"],
        preco_alvo=alerta["preco_alvo"],
        sentido=alerta["sentido"],
        notificado=bool(alerta["notificado"])
    ) for alerta in alertas]

def update_alerta_preco(user_id: int, ticker: str, novo_preco_alvo: float):
    """Atualizar alerta de preço"""
    # Determinar sentido baseado no preço atual
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        if not hist.empty:
            preco_atual = float(hist["Close"].iloc[-1])
            sentido = "UP" if novo_preco_alvo > preco_atual else "DOWN"
        else:
            sentido = "DOWN"  # Default
    except:
        sentido = "DOWN"  # Default

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE alertas_precos SET preco_alvo = ?, sentido = ?, notificado = 0 WHERE user_id = ? AND ticker = ?",
        (novo_preco_alvo, sentido, user_id, ticker)
    )
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return affected_rows > 0

def delete_alerta_preco(user_id: int, ticker: str):
    """Remover alerta de preço"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM alertas_precos WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return affected_rows > 0

def create_alerta_preco(user_id: int, ticker: str, preco_alvo: float):
    """Criar novo alerta de preço"""
    # Determinar sentido baseado no preço atual
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        if not hist.empty:
            preco_atual = float(hist["Close"].iloc[-1])
            sentido = "UP" if preco_alvo > preco_atual else "DOWN"
        else:
            sentido = "DOWN"  # Default
    except:
        sentido = "DOWN"  # Default

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO alertas_precos (user_id, ticker, preco_alvo, sentido, notificado) VALUES (?, ?, ?, ?, 0)",
            (user_id, ticker, preco_alvo, sentido)
        )
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def get_alertas_panico(user_id: int):
    """Obter alertas de pânico do usuário"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alertas_panico WHERE user_id = ?", (user_id,))
    alertas = cursor.fetchall()
    conn.close()

    return [AlertaPanico(
        ticker=alerta["ticker"],
        ativo=bool(alerta["ativo"]),
        percentual_queda=alerta["percentual_queda"]
    ) for alerta in alertas]

def update_alerta_panico(user_id: int, ticker: str, ativo: bool, percentual_queda: float):
    """Atualizar alerta de pânico"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE alertas_panico SET ativo = ?, percentual_queda = ? WHERE user_id = ? AND ticker = ?",
        (int(ativo), percentual_queda, user_id, ticker)
    )
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return affected_rows > 0

def delete_alerta_panico(user_id: int, ticker: str):
    """Remover alerta de pânico"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM alertas_panico WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return affected_rows > 0

def create_alerta_panico(user_id: int, ticker: str, percentual_queda: float):
    """Criar novo alerta de pânico"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO alertas_panico (user_id, ticker, ativo, percentual_queda) VALUES (?, ?, 1, ?)",
            (user_id, ticker, percentual_queda)
        )
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def get_historico_alertas(user_id: int):
    """Obter histórico de alertas disparados"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM alert_history WHERE user_id = ? ORDER BY triggered_at DESC",
        (user_id,)
    )
    alertas = cursor.fetchall()
    conn.close()

    return [AlertaHistorico(
        id=alerta["id"],
        ticker=alerta["ticker"],
        alert_type=alerta["alert_type"],
        trigger_value=alerta["trigger_value"],
        triggered_at=alerta["triggered_at"],
        message=alerta["message"]
    ) for alerta in alertas]

def get_configuracoes_bot(user_id: int):
    """Obter configurações do bot para o usuário"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE user_id = ?", (user_id,))
    config = cursor.fetchone()
    conn.close()

    if config:
        return ConfiguracaoBot(
            resumo_automatico=bool(config["resumo_automatico"]),
            horario_resumo=config["horario_resumo"],
            horario_panico=config["horario_panico"]
        )
    else:
        # Criar configuração padrão se não existir
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO usuarios (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
        return ConfiguracaoBot(
            resumo_automatico=True,
            horario_resumo="18:00",
            horario_panico="18:00"
        )

def update_configuracoes_bot(user_id: int, config: ConfiguracaoBot):
    """Atualizar configurações do bot"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE usuarios SET resumo_automatico = ?, horario_resumo = ?, horario_panico = ? WHERE user_id = ?",
        (int(config.resumo_automatico), config.horario_resumo, config.horario_panico, user_id)
    )
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    return affected_rows > 0

def get_dados_historicos(ticker: str, periodo: str = "1d"):
    """Obter dados históricos de uma ação"""
    try:
        stock = yf.Ticker(ticker)

        # Mapear períodos para yfinance
        period_map = {
            "1d": "1d",
            "7d": "7d",
            "1m": "1mo",
            "3m": "3mo",
            "1y": "1y",
            "all": "max"
        }

        yf_period = period_map.get(periodo, "1d")
        hist = stock.history(period=yf_period)

        if hist.empty:
            return []

        dados = []
        for date, row in hist.iterrows():
            dados.append({
                "date": date.strftime("%Y-%m-%d"),
                "price": float(row["Close"])
            })

        return dados
    except Exception as e:
        print(f"Erro ao obter dados históricos para {ticker}: {e}")
        return []

# --- Endpoints de Autenticação ---
@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    # form_data.username será o user_id e form_data.password será a dashboard_key
    try:
        user_id = int(form_data.username)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = authenticate_user(user_id, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect user ID or dashboard key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.user_id)}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

# --- Endpoints do Dashboard ---
@app.get("/dashboard/{user_id}", response_class=HTMLResponse)
async def get_dashboard_page(user_id: int):
    # Servir o arquivo HTML principal do dashboard
    try:
        with open("../frontend/index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        # Substituir placeholder do user_id no HTML
        html_content = html_content.replace("{{user_id}}", str(user_id))
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Dashboard de Ações</title>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body>
            <h1>Dashboard de Ações</h1>
            <p>Frontend em desenvolvimento...</p>
            <p>User ID: """ + str(user_id) + """</p>
        </body>
        </html>
        """)

@app.get("/api/user/me", response_model=User)
async def read_users_me(current_user: UserInDB = Depends(get_current_user)):
    return User(
        user_id=current_user.user_id,
        username=current_user.username,
        theme=current_user.theme
    )

@app.put("/api/user/update_key")
async def update_dashboard_key(
    key_update: DashboardKeyUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    # Verificar se a chave antiga está correta
    if not verify_password(key_update.old_key, current_user.hashed_dashboard_key):
        raise HTTPException(status_code=400, detail="Chave atual incorreta")

    # Atualizar com a nova chave
    new_hashed_key = get_password_hash(key_update.new_key)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE dashboard_users SET dashboard_key = ? WHERE user_id = ?",
        (new_hashed_key, current_user.user_id)
    )
    conn.commit()
    conn.close()

    return {"message": "Chave atualizada com sucesso"}

# --- Endpoints para Ações Monitoradas ---
@app.get("/api/acoes/detalhadas", response_model=List[AcaoMonitorada])
async def get_acoes_detalhadas(current_user: UserInDB = Depends(get_current_user)):
    """Obter ações monitoradas com preço atual e de referência"""
    return get_acoes_monitoradas_detalhadas(current_user.user_id)

@app.put("/api/acoes/{ticker}")
async def update_acao(
    ticker: str,
    acao_update: AcaoMonitoradaUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Editar preço de referência de uma ação monitorada"""
    success = update_acao_monitorada(current_user.user_id, ticker.upper(), acao_update.preco_referencia)
    if not success:
        raise HTTPException(status_code=404, detail="Ação não encontrada")
    return {"message": "Ação atualizada com sucesso"}

@app.delete("/api/acoes/{ticker}")
async def delete_acao(
    ticker: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """Remover ação monitorada"""
    success = delete_acao_monitorada(current_user.user_id, ticker.upper())
    if not success:
        raise HTTPException(status_code=404, detail="Ação não encontrada")
    return {"message": "Ação removida com sucesso"}

@app.post("/api/acoes")
async def create_acao(
    acao_create: AcaoMonitoradaCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Adicionar nova ação monitorada"""
    success = create_acao_monitorada(
        current_user.user_id,
        acao_create.ticker.upper(),
        acao_create.preco_referencia
    )
    if not success:
        raise HTTPException(status_code=400, detail="Erro ao adicionar ação")
    return {"message": "Ação adicionada com sucesso"}

# --- Endpoints para Alertas de Preço ---
@app.get("/api/alertas/preco", response_model=List[AlertaPreco])
async def get_alertas_preco_endpoint(current_user: UserInDB = Depends(get_current_user)):
    """Obter alertas de preço do usuário"""
    return get_alertas_preco(current_user.user_id)

@app.put("/api/alertas/preco/{ticker}")
async def update_alerta_preco_endpoint(
    ticker: str,
    alerta_update: AlertaPrecoUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Editar alerta de preço"""
    success = update_alerta_preco(current_user.user_id, ticker.upper(), alerta_update.preco_alvo)
    if not success:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    return {"message": "Alerta atualizado com sucesso"}

@app.delete("/api/alertas/preco/{ticker}")
async def delete_alerta_preco_endpoint(
    ticker: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """Remover alerta de preço"""
    success = delete_alerta_preco(current_user.user_id, ticker.upper())
    if not success:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    return {"message": "Alerta removido com sucesso"}

@app.post("/api/alertas/preco")
async def create_alerta_preco_endpoint(
    alerta_create: AlertaPrecoCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Criar novo alerta de preço"""
    success = create_alerta_preco(
        current_user.user_id,
        alerta_create.ticker.upper(),
        alerta_create.preco_alvo
    )
    if not success:
        raise HTTPException(status_code=400, detail="Erro ao criar alerta")
    return {"message": "Alerta criado com sucesso"}

# --- Endpoints para Alertas de Pânico ---
@app.get("/api/alertas/panico", response_model=List[AlertaPanico])
async def get_alertas_panico_endpoint(current_user: UserInDB = Depends(get_current_user)):
    """Obter alertas de pânico do usuário"""
    return get_alertas_panico(current_user.user_id)

@app.put("/api/alertas/panico/{ticker}")
async def update_alerta_panico_endpoint(
    ticker: str,
    alerta_update: AlertaPanicoUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Editar alerta de pânico"""
    success = update_alerta_panico(
        current_user.user_id,
        ticker.upper(),
        alerta_update.ativo,
        alerta_update.percentual_queda
    )
    if not success:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    return {"message": "Alerta atualizado com sucesso"}

@app.delete("/api/alertas/panico/{ticker}")
async def delete_alerta_panico_endpoint(
    ticker: str,
    current_user: UserInDB = Depends(get_current_user)
):
    """Remover alerta de pânico"""
    success = delete_alerta_panico(current_user.user_id, ticker.upper())
    if not success:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    return {"message": "Alerta removido com sucesso"}

@app.post("/api/alertas/panico")
async def create_alerta_panico_endpoint(
    alerta_create: AlertaPanicoCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Criar novo alerta de pânico"""
    success = create_alerta_panico(
        current_user.user_id,
        alerta_create.ticker.upper(),
        alerta_create.percentual_queda
    )
    if not success:
        raise HTTPException(status_code=400, detail="Erro ao criar alerta")
    return {"message": "Alerta criado com sucesso"}

# --- Endpoint para Histórico de Alertas ---
@app.get("/api/alertas/historico", response_model=List[AlertaHistorico])
async def get_historico_alertas_endpoint(current_user: UserInDB = Depends(get_current_user)):
    """Obter histórico de alertas disparados"""
    return get_historico_alertas(current_user.user_id)

# --- Endpoints para Configurações do Bot ---
@app.get("/api/configuracoes/bot", response_model=ConfiguracaoBot)
async def get_configuracoes_bot_endpoint(current_user: UserInDB = Depends(get_current_user)):
    """Obter configurações do bot"""
    return get_configuracoes_bot(current_user.user_id)

@app.put("/api/configuracoes/bot")
async def update_configuracoes_bot_endpoint(
    config: ConfiguracaoBot,
    current_user: UserInDB = Depends(get_current_user)
):
    """Atualizar configurações do bot"""
    success = update_configuracoes_bot(current_user.user_id, config)
    if not success:
        raise HTTPException(status_code=400, detail="Erro ao atualizar configurações")
    return {"message": "Configurações atualizadas com sucesso"}

# --- Endpoint para Dados Históricos ---
@app.get("/api/historico/{ticker}")
async def get_historico_acao(
    ticker: str,
    periodo: str = "1d",
    current_user: UserInDB = Depends(get_current_user)
):
    """Obter dados históricos de uma ação"""
    dados = get_dados_historicos(ticker.upper(), periodo)
    return {
        "ticker": ticker.upper(),
        "periodo": periodo,
        "dados": dados
    }

# --- Endpoint para Portfólio ---
@app.get("/api/portfolio", response_model=List[PortfolioPosition])
async def get_user_portfolio(current_user: UserInDB = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT ticker, quantity, avg_price FROM portfolio_positions WHERE user_id = ?",
        (current_user.user_id,)
    )
    positions_db = cursor.fetchall()
    conn.close()

    portfolio = []
    for pos in positions_db:
        ticker = pos["ticker"]
        quantity = pos["quantity"]
        avg_price = pos["avg_price"]

        # Buscar preço atual
        stock_data = get_stock_data(ticker)
        if stock_data:
            current_price = stock_data["current_price"]
            total_value = quantity * current_price
            profit_loss = (current_price - avg_price) * quantity

            portfolio.append(PortfolioPosition(
                ticker=ticker,
                quantity=quantity,
                avg_price=avg_price,
                current_price=current_price,
                total_value=total_value,
                profit_loss=profit_loss
            ))

    return portfolio

@app.post("/api/portfolio/add")
async def add_portfolio_position(
    position: PortfolioPosition,
    current_user: UserInDB = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO portfolio_positions (user_id, ticker, quantity, avg_price) VALUES (?, ?, ?, ?)",
        (current_user.user_id, position.ticker, position.quantity, position.avg_price)
    )
    conn.commit()
    conn.close()

    return {"message": f"Posição {position.ticker} adicionada/atualizada com sucesso"}

# --- Endpoint para o bot gerar a chave e o link ---
@app.get("/generate_dashboard_link/{user_id}")
async def generate_dashboard_link(user_id: int, username: str = None):
    dashboard_key = create_dashboard_user(user_id, username)
    if not dashboard_key:
        # Usuário já existe, buscar dados existentes
        user = get_user_from_db(user_id)
        if user:
            return {
                "message": "Usuário já possui acesso ao dashboard",
                "dashboard_url": f"{dominio}/dashboard/{user_id}",
                "dashboard_key": "Use sua chave existente ou solicite reset via bot"
            }
        else:
            raise HTTPException(status_code=500, detail="Erro ao criar usuário do dashboard")

    dashboard_url = f"{dominio}/dashboard/{user_id}"
    return {
        "dashboard_url": dashboard_url,
        "dashboard_key": dashboard_key,
        "message": "Dashboard criado com sucesso"
    }


def iniciar_bot():
    bot.main()


@app.on_event("startup")
def on_startup():
    thread_bot = Thread(target=iniciar_bot, daemon=True)
    thread_bot.start()

# --- Endpoint de saúde ---
@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

