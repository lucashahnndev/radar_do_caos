import sqlite3
from datetime import datetime
import pytz
import yfinance as yf
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import logging
import os

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configurações
DB_PATH = "acoes.db"
TZ = pytz.timezone("America/Sao_Paulo")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8001")

# Instância global do bot para uso nas funções agendadas
telegram_bot_instance = None

def setup_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Tabelas originais do bot
    c.execute("""
        CREATE TABLE IF NOT EXISTS acoes_monitoradas (
            user_id INTEGER,
            ticker TEXT,
            preco_referencia REAL,
            PRIMARY KEY (user_id, ticker)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id INTEGER PRIMARY KEY,
            resumo_automatico INTEGER DEFAULT 1,
            horario_resumo TEXT DEFAULT '18:00',
            horario_panico TEXT DEFAULT '18:00'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS alertas_precos (
            user_id INTEGER,
            ticker TEXT,
            preco_alvo REAL,
            sentido TEXT DEFAULT 'DOWN',
            notificado INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, ticker)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS alertas_panico (
            user_id INTEGER,
            ticker TEXT,
            ativo INTEGER DEFAULT 1,
            percentual_queda REAL DEFAULT 5.0,
            PRIMARY KEY (user_id, ticker)
        )
    """)

    # Tabelas do dashboard
    c.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_users (
            user_id INTEGER PRIMARY KEY,
            dashboard_key TEXT UNIQUE NOT NULL,
            username TEXT,
            theme TEXT DEFAULT 'dark',
            last_login TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            user_id INTEGER,
            ticker TEXT,
            quantity REAL,
            avg_price REAL,
            PRIMARY KEY (user_id, ticker)
        )
    """)

    conn.commit()
    conn.close()

# --- Comandos Telegram ---

def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    username = update.effective_user.first_name or update.effective_user.username or "Usuário"

    # Registrar usuário no banco se não existir
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO usuarios (user_id) VALUES (?)", (user_id,))

    welcome_message = f"""
🚀 *Bem-vindo ao Radar do Caos, {username}!*

📊 *Bot de Monitoramento de Ações*

*Comandos Principais:*
• `/add TICKER` - Adicionar ação para monitoramento
• `/remove TICKER` - Remover ação
• `/lista` - Listar ações monitoradas
• `/resumo` - Resumo das suas ações

*Alertas:*
• `/alerta TICKER PRECO` - Definir alerta de preço
• `/remover_alerta TICKER` - Remover alerta
• `/panico TICKER ON|OFF PERCENTUAL` - Alerta de pânico

*Configurações:*
• `/auto ON|OFF` - Ativar/desativar resumo automático
• `/horario HH:MM` - Horário do resumo diário
• `/horario_panico HH:MM` - Horário do alerta de pânico

*🌐 Dashboard Web:*
• `/dashboard` - Acesso ao painel interativo

O dashboard oferece visualizações gráficas, gestão de portfólio e configurações avançadas!
    """

    update.message.reply_text(welcome_message, parse_mode='Markdown')

def dashboard_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    username = update.effective_user.first_name or update.effective_user.username or "Usuário"
    telegram_username = update.effective_user.username

    try:
        # Fazer requisição para gerar link do dashboard
        response = requests.get(
            f"{DASHBOARD_URL}/generate_dashboard_link/{user_id}",
            params={"username": username},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            dashboard_url = data.get("dashboard_url")
            dashboard_key = data.get("dashboard_key")
            message = data.get("message", "")

            # Informações do usuário para facilitar o login
            user_info = f"""
👤 *Suas Informações:*
• ID: `{user_id}`
• Nome: {username}"""

            if telegram_username:
                user_info += f"\n• Username: @{telegram_username}"

            if "já possui acesso" in message:
                reply_text = f"""
🌐 *Dashboard - Radar do Caos*

Você já possui acesso ao dashboard!

🔗 *Link de Acesso:*
{dashboard_url}

{user_info}

🔑 Use sua chave existente ou solicite reset com `/reset_dashboard`

💡 *Dica:* Salve este link nos seus favoritos para acesso rápido!
                """
            else:
                reply_text = f"""
🌐 *Dashboard - Radar do Caos*

✅ Acesso criado com sucesso!

🔗 *Link de Acesso:*
{dashboard_url}

{user_info}

🔑 *Sua Chave de Acesso:*
`{dashboard_key}`

⚠️ *Importante:*
• Guarde sua chave em local seguro
• Use-a para fazer login no dashboard
• Você pode alterá-la depois nas configurações

💡 *Recursos do Dashboard:*
• 📊 Gráficos interativos
• 💼 Gestão de portfólio
• 🔔 Configuração de alertas
• 🎨 Temas personalizáveis
                """
        else:
            reply_text = "❌ Erro ao gerar acesso ao dashboard. Tente novamente mais tarde."

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao conectar com dashboard: {e}")
        reply_text = "❌ Erro de conexão com o dashboard. Verifique se o serviço está ativo."
    except Exception as e:
        logger.error(f"Erro inesperado no comando dashboard: {e}")
        reply_text = "❌ Erro inesperado. Tente novamente mais tarde."

    update.message.reply_text(reply_text, parse_mode='Markdown')

def reset_dashboard(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    try:
        # Remover usuário do dashboard para forçar nova criação
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM dashboard_users WHERE user_id = ?", (user_id,))

        # Chamar comando dashboard para recriar
        dashboard_command(update, context)

    except Exception as e:
        logger.error(f"Erro ao resetar dashboard: {e}")
        update.message.reply_text("❌ Erro ao resetar acesso ao dashboard.")

def add_acao(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("📝 *Uso:* `/add TICKER`\n\n*Exemplo:* `/add PETR4`", parse_mode='Markdown')
        return

    user_id = update.effective_user.id
    ticker = context.args[0].upper()

    try:
        acao = yf.Ticker(ticker)
        hist = acao.history(period="1d")
        if hist.empty:
            raise ValueError("Sem dados históricos")
        preco = float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Erro ao obter dados para {ticker}: {e}")
        update.message.reply_text(f"❌ Erro ao obter preço de *{ticker}*. Verifique se o ticker está correto.", parse_mode='Markdown')
        return

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO acoes_monitoradas VALUES (?, ?, ?)", (user_id, ticker, preco))
            c.execute("INSERT OR IGNORE INTO usuarios (user_id) VALUES (?)", (user_id,))

        update.message.reply_text(f"✅ *{ticker}* adicionada com preço referência *R$ {preco:.2f}*", parse_mode='Markdown')
        logger.info(f"Usuário {user_id} adicionou ação {ticker}")

    except Exception as e:
        logger.error(f"Erro ao salvar ação {ticker} para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao salvar ação. Tente novamente.")

def remove_acao(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("📝 *Uso:* `/remove TICKER`\n\n*Exemplo:* `/remove PETR4`", parse_mode='Markdown')
        return

    user_id = update.effective_user.id
    ticker = context.args[0].upper()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            # Verificar se a ação existe
            c.execute("SELECT ticker FROM acoes_monitoradas WHERE user_id=? AND ticker=?", (user_id, ticker))
            if not c.fetchone():
                update.message.reply_text(f"❌ *{ticker}* não está sendo monitorada.", parse_mode='Markdown')
                return

            # Remove ação monitorada
            c.execute("DELETE FROM acoes_monitoradas WHERE user_id=? AND ticker=?", (user_id, ticker))
            # Remove alertas preço vinculados
            c.execute("DELETE FROM alertas_precos WHERE user_id=? AND ticker=?", (user_id, ticker))
            # Remove alertas panico vinculados
            c.execute("DELETE FROM alertas_panico WHERE user_id=? AND ticker=?", (user_id, ticker))

        update.message.reply_text(f"✅ *{ticker}* removida e alertas associados removidos.", parse_mode='Markdown')
        logger.info(f"Usuário {user_id} removeu ação {ticker}")

    except Exception as e:
        logger.error(f"Erro ao remover ação {ticker} para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao remover ação. Tente novamente.")

def listar_acoes(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT ticker, preco_referencia FROM acoes_monitoradas WHERE user_id=?", (user_id,))
            acoes = c.fetchall()

        if not acoes:
            update.message.reply_text("📋 Nenhuma ação monitorada.\n\nUse `/add TICKER` para adicionar ações.", parse_mode='Markdown')
        else:
            message = "📋 *Ações Monitoradas:*\n\n"
            for ticker, preco_ref in acoes:
                message += f"• *{ticker}* - Ref: R$ {preco_ref:.2f}\n"
            message += f"\n💡 Total: {len(acoes)} ações"
            update.message.reply_text(message, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Erro ao listar ações para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao listar ações. Tente novamente.")

def configurar_auto(update: Update, context: CallbackContext):
    if not context.args or context.args[0].upper() not in ['ON', 'OFF']:
        update.message.reply_text("📝 *Uso:* `/auto ON` ou `/auto OFF`", parse_mode='Markdown')
        return

    status = 1 if context.args[0].upper() == "ON" else 0
    user_id = update.effective_user.id

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO usuarios (user_id) VALUES (?)", (user_id,))
            c.execute("UPDATE usuarios SET resumo_automatico=? WHERE user_id=?", (status, user_id))

        status_text = "ativado" if status else "desativado"
        update.message.reply_text(f"✅ Resumo automático *{status_text}*", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Erro ao configurar auto resumo para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao salvar configuração. Tente novamente.")

def configurar_horario(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("📝 *Uso:* `/horario HH:MM`\n\n*Exemplo:* `/horario 18:00`", parse_mode='Markdown')
        return

    horario = context.args[0]
    try:
        datetime.strptime(horario, "%H:%M")
    except ValueError:
        update.message.reply_text("❌ Formato inválido. Use *HH:MM* (exemplo: 18:00)", parse_mode='Markdown')
        return

    user_id = update.effective_user.id

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO usuarios (user_id) VALUES (?)", (user_id,))
            c.execute("UPDATE usuarios SET horario_resumo=? WHERE user_id=?", (horario, user_id))

        update.message.reply_text(f"✅ Horário do resumo diário definido para *{horario}*", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Erro ao configurar horário para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao salvar horário. Tente novamente.")

def configurar_horario_panico(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("📝 *Uso:* `/horario_panico HH:MM`\n\n*Exemplo:* `/horario_panico 18:00`", parse_mode='Markdown')
        return

    horario = context.args[0]
    try:
        datetime.strptime(horario, "%H:%M")
    except ValueError:
        update.message.reply_text("❌ Formato inválido. Use *HH:MM* (exemplo: 18:00)", parse_mode='Markdown')
        return

    user_id = update.effective_user.id

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO usuarios (user_id) VALUES (?)", (user_id,))
            c.execute("UPDATE usuarios SET horario_panico=? WHERE user_id=?", (horario, user_id))

        update.message.reply_text(f"✅ Horário do alerta de pânico definido para *{horario}*", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Erro ao configurar horário pânico para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao salvar horário. Tente novamente.")

def configurar_alerta(update: Update, context: CallbackContext):
    if len(context.args) < 2:
        update.message.reply_text("📝 *Uso:* `/alerta TICKER PRECO`\n\n*Exemplo:* `/alerta PETR4 25.50`", parse_mode='Markdown')
        return

    user_id = update.effective_user.id
    ticker = context.args[0].upper()

    try:
        preco_alvo = float(context.args[1].replace(',', '.'))
    except ValueError:
        update.message.reply_text("❌ Preço inválido. Use formato: 25.50 ou 25,50", parse_mode='Markdown')
        return

    try:
        acao = yf.Ticker(ticker)
        hist = acao.history(period="1d")
        if hist.empty:
            raise ValueError("Sem dados")
        preco_atual = float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Erro ao obter preço atual de {ticker}: {e}")
        update.message.reply_text(f"❌ Erro ao obter preço atual de *{ticker}*", parse_mode='Markdown')
        return

    sentido = "UP" if preco_alvo > preco_atual else "DOWN"

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO alertas_precos (user_id, ticker, preco_alvo, sentido, notificado)
                VALUES (?, ?, ?, ?, 0)
            """, (user_id, ticker, preco_alvo, sentido))

        direcao = "acima de" if sentido == "UP" else "abaixo de"
        update.message.reply_text(
            f"✅ Alerta de preço para *{ticker}* configurado em *R$ {preco_alvo:.2f}*\n"
            f"({direcao} R$ {preco_atual:.2f})",
            parse_mode='Markdown'
        )
        logger.info(f"Usuário {user_id} configurou alerta para {ticker} em {preco_alvo}")

    except Exception as e:
        logger.error(f"Erro ao configurar alerta para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao salvar alerta. Tente novamente.")

def remover_alerta(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("📝 *Uso:* `/remover_alerta TICKER`\n\n*Exemplo:* `/remover_alerta PETR4`", parse_mode='Markdown')
        return

    user_id = update.effective_user.id
    ticker = context.args[0].upper()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM alertas_precos WHERE user_id=? AND ticker=?", (user_id, ticker))
            if c.rowcount == 0:
                update.message.reply_text(f"❌ Nenhum alerta encontrado para *{ticker}*", parse_mode='Markdown')
            else:
                update.message.reply_text(f"✅ Alerta para *{ticker}* removido.", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Erro ao remover alerta para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao remover alerta. Tente novamente.")

def configurar_panico(update: Update, context: CallbackContext):
    if len(context.args) < 3:
        update.message.reply_text("📝 *Uso:* `/panico TICKER ON|OFF PERCENTUAL`\n\n*Exemplo:* `/panico PETR4 ON 5`", parse_mode='Markdown')
        return

    user_id = update.effective_user.id
    ticker = context.args[0].upper()
    status = context.args[1].upper()

    if status not in ["ON", "OFF"]:
        update.message.reply_text("❌ Status inválido, use *ON* ou *OFF*", parse_mode='Markdown')
        return

    try:
        percentual = float(context.args[2].replace(',', '.'))
        if percentual <= 0:
            raise ValueError("Percentual deve ser positivo")
    except ValueError:
        update.message.reply_text("❌ Percentual inválido, deve ser número positivo", parse_mode='Markdown')
        return

    ativo = 1 if status == "ON" else 0

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO alertas_panico (user_id, ticker, ativo, percentual_queda) VALUES (?, ?, ?, ?)",
                      (user_id, ticker, ativo, percentual))

        status_text = "ativado" if ativo else "desativado"
        update.message.reply_text(f"✅ Alerta de pânico para *{ticker}* {status_text} com queda de *{percentual}%*", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Erro ao configurar alerta pânico para usuário {user_id}: {e}")
        update.message.reply_text("❌ Erro ao salvar alerta. Tente novamente.")

def resumo(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    enviar_resumo(user_id, update)

# --- Lógicas de envio ---

def enviar_resumo(user_id, update=None):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT ticker FROM acoes_monitoradas WHERE user_id=?", (user_id,))
            acoes = c.fetchall()

        if not acoes:
            message = "📊 Nenhuma ação monitorada.\n\nUse `/add TICKER` para adicionar ações."
            if update:
                update.message.reply_text(message, parse_mode='Markdown')
            else:
                telegram_bot_instance.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
            return

        mensagem = "📊 *RESUMO DAS AÇÕES*\n\n"
        for (ticker,) in acoes:
            try:
                acao = yf.Ticker(ticker)
                hist = acao.history(period="7d")
                if hist.empty:
                    mensagem += f"*{ticker}*: ❌ Sem dados\n\n"
                    continue

                preco_atual = float(hist["Close"].iloc[-1])
                preco_ontem = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else preco_atual
                preco_semana = float(hist["Close"].iloc[0]) if len(hist) >= 5 else preco_ontem

                var_dia = ((preco_atual - preco_ontem) / preco_ontem) * 100
                var_semana = ((preco_atual - preco_semana) / preco_semana) * 100

                # Emojis para variação
                emoji_dia = "🟢" if var_dia >= 0 else "🔴"
                emoji_semana = "🟢" if var_semana >= 0 else "🔴"

                mensagem += (
                    f"*{ticker}*\n"
                    f"💵 R$ {preco_atual:.2f}\n"
                    f"{emoji_dia} Hoje: {'+' if var_dia >= 0 else ''}{var_dia:.2f}%\n"
                    f"{emoji_semana} Semana: {'+' if var_semana >= 0 else ''}{var_semana:.2f}%\n\n"
                )
            except Exception as e:
                logger.warning(f"Erro ao buscar dados para {ticker}: {e}")
                mensagem += f"*{ticker}*: ❌ Erro ao buscar dados\n\n"

        mensagem += "💡 Use `/dashboard` para visualizações detalhadas!"

        if update:
            update.message.reply_text(mensagem, parse_mode='Markdown')
        else:
            telegram_bot_instance.send_message(chat_id=user_id, text=mensagem, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Erro ao enviar resumo para usuário {user_id}: {e}")

def verificar_alertas_precos():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT user_id, ticker, preco_alvo, sentido FROM alertas_precos
                WHERE notificado = 0
            """)
            alertas = c.fetchall()

            for user_id, ticker, preco_alvo, sentido in alertas:
                try:
                    acao = yf.Ticker(ticker)
                    hist = acao.history(period="1d")
                    if hist.empty:
                        continue
                    preco_atual = float(hist["Close"].iloc[-1])
                except Exception as e:
                    logger.warning(f"Erro ao verificar alerta para {ticker}: {e}")
                    continue

                if (sentido == "UP" and preco_atual >= preco_alvo) or (sentido == "DOWN" and preco_atual <= preco_alvo):
                    emoji = "🚀" if sentido == "UP" else "📉"
                    telegram_bot_instance.send_message(
                        chat_id=user_id,
                        text=f"{emoji} *Alerta de preço:* {ticker} atingiu R$ {preco_atual:.2f} (alvo: R$ {preco_alvo:.2f})",
                        parse_mode='Markdown'
                    )

                    # Marca como notificado
                    c.execute("""
                        UPDATE alertas_precos SET notificado = 1 WHERE user_id = ? AND ticker = ?
                    """, (user_id, ticker))
                    conn.commit()
                    logger.info(f"Alerta de preço disparado para usuário {user_id}, ticker {ticker}")

    except Exception as e:
        logger.error(f"Erro ao verificar alertas de preço: {e}")

def verificar_alertas_panico():
    agora = datetime.now(TZ).strftime("%H:%M")

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT u.user_id, ap.ticker, ap.percentual_queda
                FROM alertas_panico ap
                JOIN usuarios u ON u.user_id = ap.user_id
                WHERE ap.ativo=1 AND u.horario_panico=?
            """, (agora,))
            rows = c.fetchall()

        for user_id, ticker, percentual in rows:
            try:
                acao = yf.Ticker(ticker)
                hist = acao.history(period="2d")
                if len(hist) < 2:
                    continue

                preco_hoje = float(hist["Close"].iloc[-1])
                preco_ontem = float(hist["Close"].iloc[-2])
                queda = ((preco_ontem - preco_hoje) / preco_ontem) * 100

                if queda >= percentual:
                    telegram_bot_instance.send_message(
                        chat_id=user_id,
                        text=(f"⚠️ *ALERTA DE PÂNICO:* {ticker} caiu {queda:.2f}% hoje, "
                              f"excedendo o limite de {percentual}% definido por você.\n\n"
                              f"💡 Acesse o `/dashboard` para análise detalhada."),
                        parse_mode='Markdown'
                    )
                    logger.info(f"Alerta de pânico disparado para usuário {user_id}, ticker {ticker}")

            except Exception as e:
                logger.warning(f"Erro ao verificar alerta pânico para {ticker}: {e}")

    except Exception as e:
        logger.error(f"Erro ao verificar alertas de pânico: {e}")

def verificar_agendamentos():
    agora = datetime.now(TZ).strftime("%H:%M")

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id FROM usuarios WHERE resumo_automatico=1 AND horario_resumo=?", (agora,))
            usuarios = c.fetchall()

        for (user_id,) in usuarios:
            enviar_resumo(user_id)
            logger.info(f"Resumo automático enviado para usuário {user_id}")

    except Exception as e:
        logger.error(f"Erro ao verificar agendamentos: {e}")

def main():
    global telegram_bot_instance

    setup_database()

    # Obter token do ambiente ou usar o hardcoded para desenvolvimento
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "7591057488:AAGOikjaLQWFPsZpkGsqIUnZEzEDsiR3vZU")

    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("Token do Telegram não configurado!")
        print("Erro: Configure a variável de ambiente TELEGRAM_BOT_TOKEN")
        return

    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    telegram_bot_instance = updater.bot

    # Registrar comandos
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("dashboard", dashboard_command))
    dispatcher.add_handler(CommandHandler("reset_dashboard", reset_dashboard))
    dispatcher.add_handler(CommandHandler("add", add_acao))
    dispatcher.add_handler(CommandHandler("remove", remove_acao))
    dispatcher.add_handler(CommandHandler("lista", listar_acoes))
    dispatcher.add_handler(CommandHandler("resumo", resumo))
    dispatcher.add_handler(CommandHandler("auto", configurar_auto))
    dispatcher.add_handler(CommandHandler("horario", configurar_horario))
    dispatcher.add_handler(CommandHandler("horario_panico", configurar_horario_panico))
    dispatcher.add_handler(CommandHandler("alerta", configurar_alerta))
    dispatcher.add_handler(CommandHandler("remover_alerta", remover_alerta))
    dispatcher.add_handler(CommandHandler("panico", configurar_panico))

    # Configurar agendador
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.add_job(verificar_agendamentos, "interval", minutes=10)  # Reduzido para 10 minutos
    scheduler.add_job(verificar_alertas_precos, "interval", minutes=5)  # Reduzido para 5 minutos
    scheduler.add_job(verificar_alertas_panico, "interval", minutes=5)  # Reduzido para 5 minutos
    scheduler.start()

    logger.info("Bot iniciado com sucesso!")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()


# Função para salvar alerta no histórico
def salvar_alerta_historico(user_id, ticker, alert_type, trigger_value, message):
    """Salvar alerta no histórico"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO alert_history (user_id, ticker, alert_type, trigger_value, triggered_at, message)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, ticker, alert_type, trigger_value, datetime.now().isoformat(), message))
            conn.commit()
    except Exception as e:
        logger.error(f"Erro ao salvar alerta no histórico: {e}")

# Atualizar função de verificação de alertas de preço
def verificar_alertas_precos():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT user_id, ticker, preco_alvo, sentido FROM alertas_precos
                WHERE notificado = 0
            """)
            alertas = c.fetchall()

            for user_id, ticker, preco_alvo, sentido in alertas:
                try:
                    acao = yf.Ticker(ticker)
                    hist = acao.history(period="1d")
                    if hist.empty:
                        continue
                    preco_atual = float(hist["Close"].iloc[-1])
                except Exception as e:
                    logger.warning(f"Erro ao verificar alerta para {ticker}: {e}")
                    continue

                if (sentido == "UP" and preco_atual >= preco_alvo) or (sentido == "DOWN" and preco_atual <= preco_alvo):
                    emoji = "🚀" if sentido == "UP" else "📉"
                    message = f"{emoji} *Alerta de preço:* {ticker} atingiu R$ {preco_atual:.2f} (alvo: R$ {preco_alvo:.2f})"

                    telegram_bot_instance.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode='Markdown'
                    )

                    # Salvar no histórico
                    salvar_alerta_historico(user_id, ticker, "price", preco_atual, message)

                    # Marca como notificado
                    c.execute("""
                        UPDATE alertas_precos SET notificado = 1 WHERE user_id = ? AND ticker = ?
                    """, (user_id, ticker))
                    conn.commit()
                    logger.info(f"Alerta de preço disparado para usuário {user_id}, ticker {ticker}")

    except Exception as e:
        logger.error(f"Erro ao verificar alertas de preço: {e}")

# Atualizar função de verificação de alertas de pânico
def verificar_alertas_panico():
    agora = datetime.now(TZ).strftime("%H:%M")

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT u.user_id, ap.ticker, ap.percentual_queda
                FROM alertas_panico ap
                JOIN usuarios u ON u.user_id = ap.user_id
                WHERE ap.ativo=1 AND u.horario_panico=?
            """, (agora,))
            alertas = c.fetchall()

            for user_id, ticker, percentual_queda in alertas:
                try:
                    acao = yf.Ticker(ticker)
                    hist = acao.history(period="7d")
                    if len(hist) < 2:
                        continue

                    preco_atual = float(hist["Close"].iloc[-1])
                    preco_anterior = float(hist["Close"].iloc[-2])
                    queda_real = ((preco_anterior - preco_atual) / preco_anterior) * 100

                    if queda_real >= percentual_queda:
                        message = f"🚨 *ALERTA DE PÂNICO:* {ticker} caiu {queda_real:.2f}% (R$ {preco_atual:.2f})"

                        telegram_bot_instance.send_message(
                            chat_id=user_id,
                            text=message,
                            parse_mode='Markdown'
                        )

                        # Salvar no histórico
                        salvar_alerta_historico(user_id, ticker, "panic", queda_real, message)

                        logger.info(f"Alerta de pânico disparado para usuário {user_id}, ticker {ticker}, queda {queda_real:.2f}%")

                except Exception as e:
                    logger.warning(f"Erro ao verificar alerta de pânico para {ticker}: {e}")

    except Exception as e:
        logger.error(f"Erro ao verificar alertas de pânico: {e}")

