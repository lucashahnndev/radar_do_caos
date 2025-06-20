// Configuração da aplicação
const API_BASE_URL = '';
let historicoChartInstance = null;
function dashboardApp() {
    return {
        // Estado da aplicação
        loading: true,
        isAuthenticated: false,
        loginLoading: false,
        loginError: '',
        refreshing: false,
        currentView: 'overview',
        theme: 'dark',


        // Dados do usuário
        user: {},
        token: '',

        // Formulário de login
        loginForm: {
            userId: '',
            dashboardKey: ''
        },

        // Dados principais
        acoesDetalhadas: [],
        portfolio: [],
        alertasPreco: [],
        alertasPanico: [],
        historicoAlertas: [],
        botConfig: {
            resumo_automatico: true,
            horario_resumo: '18:00',
            horario_panico: '18:00'
        },

        // Modais
        showAddAcaoModal: false,
        showEditAcaoModal: false,
        showAddAlertModal: false,
        showAddPanicoModal: false,
        showChangeKeyModal: false,
        showAddPositionModal: false,

        // Formulários
        newAcao: { ticker: '', preco_referencia: null },
        editingAcao: { ticker: '', preco_referencia: 0 },
        newAlerta: { ticker: '', preco_alvo: 0 },
        newPanico: { ticker: '', percentual_queda: 5 },

        // Filtros de histórico
        historicoFilters: {
            ticker: '',
            periodo: '7d'
        },
        historicoData: [],

        // Charts
        performanceChart: null,
        portfolioChart: null,
        //historicoChart: null, // Certifique-se de que está null inicialmente

        // Inicialização
        async init() {
            this.loading = true;

            // Verificar se há token salvo
            const savedToken = localStorage.getItem('dashboard_token');
            const savedTheme = localStorage.getItem('dashboard_theme');

            if (savedTheme) {
                this.theme = savedTheme;
            }

            if (savedToken) {
                this.token = savedToken;
                try {
                    await this.loadUserData();
                    this.isAuthenticated = true;
                    await this.loadAllData();
                } catch (error) {
                    console.error('Erro ao carregar dados do usuário:', error);
                    localStorage.removeItem('dashboard_token');
                    this.token = '';
                }
            }

            this.loading = false;
        },

        // Autenticação
        async login() {
            this.loginLoading = true;
            this.loginError = '';

            try {
                const formData = new FormData();
                formData.append('username', this.loginForm.userId);
                formData.append('password', this.loginForm.dashboardKey);

                const response = await fetch(`${API_BASE_URL}/token`, {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) {
                    throw new Error('Credenciais inválidas');
                }

                const data = await response.json();
                this.token = data.access_token;
                localStorage.setItem('dashboard_token', this.token);

                await this.loadUserData();
                this.isAuthenticated = true;
                await this.loadAllData();

            } catch (error) {
                this.loginError = error.message;
            } finally {
                this.loginLoading = false;
            }
        },

        logout() {
            this.isAuthenticated = false;
            this.token = '';
            this.user = {};
            localStorage.removeItem('dashboard_token');
            this.loginForm = { userId: '', dashboardKey: '' };
        },

        // Carregamento de dados
        async loadUserData() {
            const response = await this.apiCall('/api/user/me');
            this.user = response;
        },

        async loadAllData() {
            await Promise.all([
                this.loadAcoesDetalhadas(),
                this.loadPortfolio(),
                this.loadAlertasPreco(),
                this.loadAlertasPanico(),
                this.loadHistoricoAlertas(),
                this.loadBotConfig()
            ]);

            this.$nextTick(() => {
                this.initCharts();
            });
        },

        async loadAcoesDetalhadas() {
            try {
                this.acoesDetalhadas = await this.apiCall('/api/acoes/detalhadas');
            } catch (error) {
                console.error('Erro ao carregar ações detalhadas:', error);
                this.acoesDetalhadas = [];
            }
        },

        async loadPortfolio() {
            try {
                this.portfolio = await this.apiCall('/api/portfolio');
            } catch (error) {
                console.error('Erro ao carregar portfólio:', error);
                this.portfolio = [];
            }
        },

        async loadAlertasPreco() {
            try {
                this.alertasPreco = await this.apiCall('/api/alertas/preco');
            } catch (error) {
                console.error('Erro ao carregar alertas de preço:', error);
                this.alertasPreco = [];
            }
        },

        async loadAlertasPanico() {
            try {
                this.alertasPanico = await this.apiCall('/api/alertas/panico');
            } catch (error) {
                console.error('Erro ao carregar alertas de pânico:', error);
                this.alertasPanico = [];
            }
        },

        async loadHistoricoAlertas() {
            try {
                this.historicoAlertas = await this.apiCall('/api/alertas/historico');
            } catch (error) {
                console.error('Erro ao carregar histórico de alertas:', error);
                this.historicoAlertas = [];
            }
        },

        async loadBotConfig() {
            try {
                this.botConfig = await this.apiCall('/api/configuracoes/bot');
            } catch (error) {
                console.error('Erro ao carregar configurações do bot:', error);
            }
        },

        async loadHistoricoData() {
            if (!this.historicoFilters.ticker) return;

            try {
                const response = await this.apiCall(
                    `/api/historico/${this.historicoFilters.ticker}?periodo=${this.historicoFilters.periodo}`
                );

                // ---- INÍCIO DA MODIFICAÇÃO ----
                // Passa os dados diretamente para a função de atualização
                this.updateHistoricoChart(response.dados);
                // ---- FIM DA MODIFICAÇÃO ----

            } catch (error) {
                console.error('Erro ao carregar dados históricos:', error);
            }
        },


        // Gerenciamento de ações
        async addAcao() {
            try {
                await this.apiCall('/api/acoes', 'POST', this.newAcao);
                this.showAddAcaoModal = false;
                this.newAcao = { ticker: '', preco_referencia: null };
                await this.loadAcoesDetalhadas();
                this.showSuccess('Ação adicionada com sucesso!');
            } catch (error) {
                this.showError('Erro ao adicionar ação: ' + error.message);
            }
        },

        editAcao(acao) {
            this.editingAcao = { ...acao };
            this.showEditAcaoModal = true;
        },

        async updateAcao() {
            try {
                await this.apiCall(`/api/acoes/${this.editingAcao.ticker}`, 'PUT', {
                    preco_referencia: this.editingAcao.preco_referencia
                });
                this.showEditAcaoModal = false;
                await this.loadAcoesDetalhadas();
                this.showSuccess('Ação atualizada com sucesso!');
            } catch (error) {
                this.showError('Erro ao atualizar ação: ' + error.message);
            }
        },

        async deleteAcao(ticker) {
            if (!confirm(`Tem certeza que deseja remover a ação ${ticker}?`)) return;

            try {
                await this.apiCall(`/api/acoes/${ticker}`, 'DELETE');
                await this.loadAcoesDetalhadas();
                this.showSuccess('Ação removida com sucesso!');
            } catch (error) {
                this.showError('Erro ao remover ação: ' + error.message);
            }
        },

        // Gerenciamento de alertas de preço
        async addAlertaPreco() {
            try {
                await this.apiCall('/api/alertas/preco', 'POST', this.newAlerta);
                this.showAddAlertModal = false;
                this.newAlerta = { ticker: '', preco_alvo: 0 };
                await this.loadAlertasPreco();
                this.showSuccess('Alerta adicionado com sucesso!');
            } catch (error) {
                this.showError('Erro ao adicionar alerta: ' + error.message);
            }
        },

        editAlertaPreco(alerta) {
            const novoPreco = prompt(`Novo preço alvo para ${alerta.ticker}:`, alerta.preco_alvo);
            if (novoPreco && !isNaN(novoPreco)) {
                this.updateAlertaPreco(alerta.ticker, parseFloat(novoPreco));
            }
        },

        async updateAlertaPreco(ticker, novoPreco) {
            try {
                await this.apiCall(`/api/alertas/preco/${ticker}`, 'PUT', {
                    preco_alvo: novoPreco
                });
                await this.loadAlertasPreco();
                this.showSuccess('Alerta atualizado com sucesso!');
            } catch (error) {
                this.showError('Erro ao atualizar alerta: ' + error.message);
            }
        },

        async deleteAlertaPreco(ticker) {
            if (!confirm(`Tem certeza que deseja remover o alerta de ${ticker}?`)) return;

            try {
                await this.apiCall(`/api/alertas/preco/${ticker}`, 'DELETE');
                await this.loadAlertasPreco();
                this.showSuccess('Alerta removido com sucesso!');
            } catch (error) {
                this.showError('Erro ao remover alerta: ' + error.message);
            }
        },

        // Gerenciamento de alertas de pânico
        async addAlertaPanico() {
            try {
                await this.apiCall('/api/alertas/panico', 'POST', this.newPanico);
                this.showAddPanicoModal = false;
                this.newPanico = { ticker: '', percentual_queda: 5 };
                await this.loadAlertasPanico();
                this.showSuccess('Alerta de pânico adicionado com sucesso!');
            } catch (error) {
                this.showError('Erro ao adicionar alerta de pânico: ' + error.message);
            }
        },

        editAlertaPanico(alerta) {
            const novoPercentual = prompt(`Novo percentual de queda para ${alerta.ticker}:`, alerta.percentual_queda);
            if (novoPercentual && !isNaN(novoPercentual)) {
                this.updateAlertaPanico(alerta.ticker, true, parseFloat(novoPercentual));
            }
        },

        async updateAlertaPanico(ticker, ativo, percentual) {
            try {
                await this.apiCall(`/api/alertas/panico/${ticker}`, 'PUT', {
                    ativo: ativo,
                    percentual_queda: percentual
                });
                await this.loadAlertasPanico();
                this.showSuccess('Alerta de pânico atualizado com sucesso!');
            } catch (error) {
                this.showError('Erro ao atualizar alerta de pânico: ' + error.message);
            }
        },

        async deleteAlertaPanico(ticker) {
            if (!confirm(`Tem certeza que deseja remover o alerta de pânico de ${ticker}?`)) return;

            try {
                await this.apiCall(`/api/alertas/panico/${ticker}`, 'DELETE');
                await this.loadAlertasPanico();
                this.showSuccess('Alerta de pânico removido com sucesso!');
            } catch (error) {
                this.showError('Erro ao remover alerta de pânico: ' + error.message);
            }
        },

        // Configurações
        async updateBotConfig() {
            try {
                await this.apiCall('/api/configuracoes/bot', 'PUT', this.botConfig);
                this.showSuccess('Configurações atualizadas com sucesso!');
            } catch (error) {
                this.showError('Erro ao atualizar configurações: ' + error.message);
            }
        },

        updateTheme() {
            localStorage.setItem('dashboard_theme', this.theme);
        },

        toggleTheme() {
            this.theme = this.theme === 'dark' ? 'light' : 'dark';
            this.updateTheme();
        },

        // Utilitários
        async refreshData() {
            this.refreshing = true;
            try {
                await this.loadAllData();
            } finally {
                this.refreshing = false;
            }
        },

        async apiCall(endpoint, method = 'GET', data = null) {
            const options = {
                method,
                headers: {
                    'Authorization': `Bearer ${this.token}`,
                    'Content-Type': 'application/json'
                }
            };

            if (data && method !== 'GET') {
                options.body = JSON.stringify(data);
            }

            const response = await fetch(`${API_BASE_URL}${endpoint}`, options);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `Erro ${response.status}`);
            }

            return await response.json();
        },

        // Formatação
        formatCurrency(value) {
            if (value === null || value === undefined) return 'N/A';
            return new Intl.NumberFormat('pt-BR', {
                style: 'currency',
                currency: 'BRL'
            }).format(value);
        },

        formatPercentage(value) {
            if (value === null || value === undefined) return 'N/A';
            const sign = value >= 0 ? '+' : '';
            return `${sign}${value.toFixed(2)}%`;
        },

        formatDateTime(dateString) {
            return new Date(dateString).toLocaleString('pt-BR');
        },

        getVariationClass(variation) {
            if (variation === null || variation === undefined) return '';
            return variation >= 0 ? 'positive' : 'negative';
        },

        getViewTitle() {
            const titles = {
                'overview': 'Visão Geral',
                'acoes': 'Ações Monitoradas',
                'historico': 'Histórico de Ações',
                'portfolio': 'Meu Portfólio',
                'alerts': 'Alertas',
                'alert-history': 'Histórico de Alertas',
                'settings': 'Configurações'
            };
            return titles[this.currentView] || 'Dashboard';
        },

        getTotalAlerts() {
            return this.alertasPreco.length + this.alertasPanico.filter(a => a.ativo).length;
        },

        getTotalPortfolioValue() {
            return this.portfolio.reduce((total, position) => {
                return total + (position.quantity * position.avg_price);
            }, 0);
        },

        // Notificações
        showSuccess(message) {
            // Implementar sistema de notificações
            alert(message);
        },

        showError(message) {
            // Implementar sistema de notificações
            alert(message);
        },

        // Charts

        initCharts() {
            this.initPerformanceChart();
            this.initPortfolioChart();
            this.initHistoricoChart(); // Chama a inicialização aqui
        },

        initPerformanceChart() {
            const ctx = document.getElementById('performanceChart');
            if (!ctx || this.performanceChart) return;

            const data = this.acoesDetalhadas.map(acao => ({
                ticker: acao.ticker,
                variacao: acao.variacao_percentual || 0
            }));

            this.performanceChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.map(d => d.ticker),
                    datasets: [{
                        label: 'Variação (%)',
                        data: data.map(d => d.variacao),
                        backgroundColor: data.map(d => d.variacao >= 0 ? '#10b981' : '#ef4444'),
                        borderColor: data.map(d => d.variacao >= 0 ? '#059669' : '#dc2626'),
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            display: false
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                callback: function (value) {
                                    return value + '%';
                                }
                            }
                        }
                    }
                }
            });
        },

        initPortfolioChart() {
            const ctx = document.getElementById('portfolioChart');
            if (!ctx || this.portfolioChart) return;

            const data = this.portfolio.map(position => ({
                ticker: position.ticker,
                value: position.quantity * position.avg_price
            }));

            this.portfolioChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: data.map(d => d.ticker),
                    datasets: [{
                        data: data.map(d => d.value),
                        backgroundColor: [
                            '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
                            '#06b6d4', '#84cc16', '#f97316', '#ec4899', '#6366f1'
                        ]
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            position: 'bottom'
                        }
                    }
                }
            });
        },

        // ... (initCharts continua igual)

        initHistoricoChart() {
            const ctx = document.getElementById('historicoChart');
            if (!ctx) return;

            // Destrói a instância anterior, se existir
            if (historicoChartInstance) {
                historicoChartInstance.destroy();
            }

            // Cria a nova instância do gráfico usando a variável global
            historicoChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [], // Inicia vazio
                    datasets: [{
                        label: 'Preço',
                        data: [], // Inicia vazio
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                        tension: 0.1
                    }]
                },
                options: {
                    responsive: true,
                    scales: {
                        y: {
                            beginAtZero: false,
                            ticks: {
                                callback: function (value) {
                                    return 'R$ ' + value.toFixed(2);
                                }
                            }
                        }
                    }
                }
            });
        },

        updateHistoricoChart(rawData) { // Recebe os dados como argumento
            // Verifica se a instância global existe e se recebemos dados válidos
            if (!historicoChartInstance || !Array.isArray(rawData)) {
                console.warn('Gráfico histórico não inicializado ou dados inválidos para atualização.');
                return;
            }

            try {
                // Processa os dados puros recebidos
                const labels = rawData.map(d => d.date);
                const data = rawData.map(d => d.price);

                // Atualiza a instância global diretamente
                historicoChartInstance.data.labels = labels;
                historicoChartInstance.data.datasets[0].data = data;
                historicoChartInstance.data.datasets[0].label = `${this.historicoFilters.ticker} - Preço`;
                historicoChartInstance.update(); // Apenas atualiza os dados
            } catch (err) {
                console.error('Erro ao atualizar gráfico:', err);
            }
        }


    };
}

