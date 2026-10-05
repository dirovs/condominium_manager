// CondoManager - Frontend Logic
// Integrates statistics, charts, tables, forms, and WhatsApp simulator.

// Intercept fetch globally to pass X-User-Role header
const originalFetch = window.fetch;
window.fetch = function(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    if (currentUser && currentUser.role) {
        if (options.headers instanceof Headers) {
            if (!options.headers.has('X-User-Role')) {
                options.headers.set('X-User-Role', currentUser.role);
            }
        } else if (Array.isArray(options.headers)) {
            options.headers.push(['X-User-Role', currentUser.role]);
        } else {
            if (!options.headers['X-User-Role']) {
                options.headers['X-User-Role'] = currentUser.role;
            }
        }
    }
    return originalFetch(url, options);
};

let currentTab = 'resumen';
let summaryStats = {};
let aliquotsData = [];
let expensesData = [];
let otherIncomesData = [];
let boardMembersData = [];
let debtorsData = [];
let additionalChargesData = [];
let bankStatementData = [];
let settingsData = {};
let unitsData = [];
let unitsSortKey = 'id';
let unitsSortAsc = true;
let editingUnitId = null;
let editingExpenseId = null;
let editingOtherIncomeId = null;

// Pagination variables
let aliquotsPage = 1;
const aliquotsLimit = 10;
let expensesPage = 1;
const expensesLimit = 10;
let otherIncomesPage = 1;
const otherIncomesLimit = 10;
let bankPage = 1;
const bankLimit = 10;


// Charts instances
let cashflowChart = null;
let expensesChart = null;

// File Upload variables
let attachedFile = null;
let uploadingAliquotId = null;

// Interactive reconciliation state
let selectedPayment = null; 
let selectedBankTx = null;
let statementUploadedFile = null;

// Global User state
let currentUser = null;

// Initialize App
document.addEventListener('DOMContentLoaded', () => {
    // Initialise Lucide Icons
    lucide.createIcons();
    
    // Set default dates to forms
    const todayStr = new Date().toISOString().split('T')[0];
    document.getElementById('expense-date').value = todayStr;
    document.getElementById('bank-date').value = todayStr;
    document.getElementById('other-income-date').value = todayStr;
    
    // Tab Switching
    document.querySelectorAll('.top-nav .nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const targetTab = item.getAttribute('data-tab');
            switchTab(targetTab);
        });
    });

    // Close modals on clicking outside of modal-content
    window.addEventListener('click', (event) => {
        if (event.target.classList.contains('modal')) {
            event.target.classList.remove('open');
        }
        const dropdown = document.getElementById('profile-dropdown');
        if (dropdown && !event.target.closest('.user-profile')) {
            dropdown.style.display = 'none';
        }
    });

    // Form Event Listeners
    document.getElementById('form-add-expense').addEventListener('submit', handleAddExpense);
    document.getElementById('form-add-other-income').addEventListener('submit', handleAddOtherIncome);
    document.getElementById('form-add-board-member').addEventListener('submit', handleAddBoardMember);
    document.getElementById('form-add-charge').addEventListener('submit', handleAddCharge);
    document.getElementById('form-add-bank').addEventListener('submit', handleAddBankTransaction);
    document.getElementById('form-settings-google').addEventListener('submit', handleSaveGoogleSettings);
    document.getElementById('form-settings-rules').addEventListener('submit', handleSaveRulesSettings);
    document.getElementById('form-whatsapp-chat').addEventListener('submit', handleSendWhatsapp);
    document.getElementById('form-reconcile-manual').addEventListener('submit', handleSubmitManualValidation);
    document.getElementById('form-upload-receipt-manual').addEventListener('submit', handleUploadReceiptManual);
    document.getElementById('form-add-unit').addEventListener('submit', handleAddUnit);
    document.getElementById('form-settings-initial-balance').addEventListener('submit', handleSaveInitialBalance);
    document.getElementById('form-add-initial-debt').addEventListener('submit', handleAddInitialDebt);
    document.getElementById('form-edit-charge').addEventListener('submit', handleEditChargeSubmit);
    document.getElementById('form-edit-aliquot').addEventListener('submit', handleEditAliquotSubmit);
    document.getElementById('form-edit-bank-transaction').addEventListener('submit', handleEditBankTransactionSubmit);
    document.getElementById('form-settings-notifications').addEventListener('submit', handleSaveNotificationSettings);
    document.getElementById('btn-test-smtp')?.addEventListener('click', handleTestSmtpConnection);
    document.getElementById('form-settings-database').addEventListener('submit', handleSaveDatabaseSettings);
    
    // Config change visibility toggles
    document.getElementById('config-whatsapp-provider').addEventListener('change', toggleWhatsappSettingsVisibility);
    document.getElementById('config-db-type').addEventListener('change', toggleDatabaseSettingsVisibility);
    
    // Board Member Exoneration Checkbox Auto-Save
    document.getElementById('config-exonerate-directiva').addEventListener('change', async function() {
        const exonerate_directiva = this.checked;
        const res = await saveSettings({ exonerate_directiva });
        if (res && res.ok) {
            showToast(`Regla de exoneración ${exonerate_directiva ? 'activada' : 'desactivada'} correctamente.`);
            loadAllData();
        } else {
            showToast("Error al guardar regla de exoneración.", "error");
            this.checked = !exonerate_directiva;
        }
    });
    
    // Attached file listener
    document.getElementById('chat-file-upload').addEventListener('change', handleFileSelected);
    
    // Search Aliquots Listener
    document.getElementById('filter-aliquot-search').addEventListener('input', () => {
        aliquotsPage = 1;
        renderAliquotsTable();
    });
    document.getElementById('filter-aliquot-month').addEventListener('change', () => {
        aliquotsPage = 1;
        renderAliquotsTable();
    });
    document.getElementById('filter-aliquot-year').addEventListener('change', () => {
        aliquotsPage = 1;
        renderAliquotsTable();
    });
    document.getElementById('filter-aliquot-status').addEventListener('change', () => {
        aliquotsPage = 1;
        renderAliquotsTable();
    });

    // Initialize Drag & Drop statement upload
    initStatementUpload();

    // Session Checking & URL Actions
    const urlParams = new URLSearchParams(window.location.search);
    const action = urlParams.get('action');
    if (action === 'reset') {
        const cedula = urlParams.get('cedula');
        const role = urlParams.get('role');
        document.getElementById('reset-cedula').value = cedula;
        document.getElementById('reset-role').value = role;
        toggleLoginScreen(true);
        setLoginMode('reset');
    } else {
        currentUser = JSON.parse(localStorage.getItem('currentUser'));
        if (currentUser) {
            toggleLoginScreen(false);
            applyRoleAccess(currentUser);
            loadAllData();
        } else {
            toggleLoginScreen(true);
            onLoginRoleChange();
        }
    }
});

// Toast Notifications Helper
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let icon = 'check-circle';
    if (type === 'warning') icon = 'alert-triangle';
    if (type === 'error') icon = 'alert-circle';
    
    toast.innerHTML = `
        <i data-lucide="${icon}"></i>
        <div>${message}</div>
    `;
    container.appendChild(toast);
    lucide.createIcons();
    
    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Switch Tabs
function switchTab(tabId) {
    if (currentUser && (currentUser.role === 'owner' || currentUser.role === 'tenant')) {
        tabId = 'mis-finanzas';
    }

    // Update active nav item
    document.querySelectorAll('.top-nav .nav-item').forEach(item => {
        item.classList.remove('active');
        if (item.getAttribute('data-tab') === tabId) {
            item.classList.add('active');
        }
    });

    // Handle submenu active parent state
    document.querySelectorAll('.top-nav .nav-menu-item').forEach(menuItem => {
        menuItem.classList.remove('active-parent');
        const activeChild = menuItem.querySelector(`.nav-item[data-tab="${tabId}"]`);
        if (activeChild) {
            menuItem.classList.add('active-parent');
        }
    });

    // Update active content section
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active-tab');
    });
    
    const targetSection = document.getElementById(`tab-${tabId}`);
    if (targetSection) {
        targetSection.classList.add('active-tab');
    }

    currentTab = tabId;
    
    // Update header title
    const titles = {
        'resumen': 'Resumen General',
        'departamentos': 'Directorio de Departamentos',
        'alicuotas': 'Control de Alícuotas',
        'gastos': 'Registro y Control de Gastos',
        'deudores': 'Control de Deudores y Cargos Adicionales',
        'banco': 'Estado de Cuenta Bancario',
        'reportes': 'Reportes Financieros',
        'whatsapp': 'Procesamiento e Integración WhatsApp',
        'config': 'Configuración del Sistema',
        'admins': 'Gestión de Administradores',
        'mis-finanzas': 'Mis Finanzas y Pagos'
    };
    const title = titles[tabId] || 'CondoManager';
    const vt = document.getElementById('view-title');
    if (vt) vt.innerText = title;
    const vtm = document.getElementById('view-title-main');
    if (vtm) vtm.innerText = title;

    // Load admin list when switching to admins tab
    if (tabId === 'admins') {
        loadAdmins();
    }
    // Render condomino obligations list when switching to mis-finanzas
    if (tabId === 'mis-finanzas') {
        renderMyObligations();
    }
    // Initialize reports tab
    if (tabId === 'reportes') {
        initReportesTab();
    }
}

// Modal Control Helpers
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('open');
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('open');
        
        if (modalId === 'modal-add-expense') {
            editingExpenseId = null;
            const titleEl = document.getElementById('modal-expense-title');
            const btnEl = document.getElementById('btn-submit-expense');
            if (titleEl) titleEl.innerText = "Registrar Nuevo Gasto";
            if (btnEl) btnEl.innerText = "Registrar Gasto";
            document.getElementById('form-add-expense').reset();
            const todayStr = new Date().toISOString().split('T')[0];
            document.getElementById('expense-date').value = todayStr;
        }
        if (modalId === 'modal-add-other-income') {
            editingOtherIncomeId = null;
            const titleEl = document.getElementById('modal-other-income-title');
            const btnEl = document.getElementById('btn-submit-other-income');
            if (titleEl) titleEl.innerText = "Registrar Otro Ingreso (General)";
            if (btnEl) btnEl.innerText = "Registrar Ingreso";
            document.getElementById('form-add-other-income').reset();
            const todayStr = new Date().toISOString().split('T')[0];
            document.getElementById('other-income-date').value = todayStr;
            document.getElementById('other-income-reference').value = "";
        }
    }
}

// Fetch all APIs
async function loadAllData() {
    try {
        await fetchSummary();
        await fetchSettings();
        await fetchUnits();
        await fetchAliquots();
        await fetchExpenses();
        await fetchOtherIncomes();
        await fetchBoardMembers();
        await fetchDebtors();
        await fetchAdditionalCharges();
        await fetchBankStatement();
        await loadNotificationLogs();
        
        // Populate Unit selection lists
        populateUnitDropdowns();
        
        // Count manual validations pending for sidebar badge
        updateValidationBadge();
        
        // Build Dashboard Charts
        initCharts();
        
        // Render reconciliation lists
        renderReconciliationLists();
        
        // Load role specific UI data
        if (currentUser && currentUser.role === 'superadmin') {
            await loadAdmins();
        }
        if (currentUser && (currentUser.role === 'owner' || currentUser.role === 'tenant')) {
            renderMyObligations();
        }
    } catch (error) {
        console.error("Error loading data:", error);
        showToast("Error de conexión con el servidor.", "error");
    }
}

async function fetchSummary() {
    const res = await fetch('/api/summary');
    summaryStats = await res.json();
    
    const currency = summaryStats.currency || '$';
    
    if (currentUser && (currentUser.role === 'owner' || currentUser.role === 'tenant')) {
        // Calculate personal stats
        const personalPaid = aliquotsData
            .filter(a => String(a.unit) === String(currentUser.unit_id) && a.status !== 'Exonerado')
            .reduce((sum, a) => {
                let paid = 0;
                if (a.paid_amount && a.paid_amount > 0) {
                    paid = a.paid_amount + (a.status === 'Pagado' ? (a.late_fee || 0) : 0);
                } else if (a.status === 'Pagado') {
                    paid = (a.amount || 0) + (a.late_fee || 0);
                }
                return sum + paid;
            }, 0) +
            additionalChargesData
            .filter(c => String(c.unit) === String(currentUser.unit_id) && c.status !== 'Exonerado')
            .reduce((sum, c) => {
                let paid = 0;
                if (c.paid_amount && c.paid_amount > 0) {
                    paid = c.paid_amount;
                } else if (c.status === 'Pagado') {
                    paid = (c.amount || 0);
                }
                return sum + paid;
            }, 0);

        const personalDebt = aliquotsData
            .filter(a => String(a.unit) === String(currentUser.unit_id) && a.status !== 'Pagado' && a.status !== 'Exonerado')
            .reduce((sum, a) => sum + (a.amount || 0) + (a.late_fee || 0), 0) +
            additionalChargesData
            .filter(c => String(c.unit) === String(currentUser.unit_id) && c.status !== 'Pagado' && c.status !== 'Exonerado')
            .reduce((sum, c) => sum + (c.amount || 0), 0);

        document.getElementById('kpi-revenue').innerText = `${currency}${personalPaid.toFixed(2)}`;
        document.getElementById('kpi-expenses').innerText = `${currency}${summaryStats.total_expenses.toFixed(2)}`;
        document.getElementById('kpi-balance').innerText = `${currency}${summaryStats.net_balance.toFixed(2)}`;
        document.getElementById('kpi-debt').innerText = `${currency}${personalDebt.toFixed(2)}`;
        
        const debtLabel = document.getElementById('kpi-debtors-count');
        if (personalDebt > 0) {
            debtLabel.innerText = `Posee Deudas Pendientes`;
            debtLabel.className = "kpi-label text-red";
        } else {
            debtLabel.innerText = `Al día con el Condominio`;
            debtLabel.className = "kpi-label text-green";
        }
        
        const progressCard = document.querySelector('.progress-card');
        if (progressCard) progressCard.style.display = 'none';
        
        const chartsGrid = document.querySelector('.charts-grid');
        if (chartsGrid) chartsGrid.style.display = 'none';
    } else {
        document.getElementById('kpi-revenue').innerText = `${currency}${summaryStats.total_revenue.toFixed(2)}`;
        document.getElementById('kpi-expenses').innerText = `${currency}${summaryStats.total_expenses.toFixed(2)}`;
        document.getElementById('kpi-balance').innerText = `${currency}${summaryStats.net_balance.toFixed(2)}`;
        document.getElementById('kpi-debt').innerText = `${currency}${summaryStats.total_debt.toFixed(2)}`;
        
        const debtLabel = document.getElementById('kpi-debtors-count');
        debtLabel.innerText = `${summaryStats.debtors_count} Propietarios en Mora`;
        debtLabel.className = "kpi-label";
        
        const progressCard = document.querySelector('.progress-card');
        if (progressCard) progressCard.style.display = 'block';
        
        const chartsGrid = document.querySelector('.charts-grid');
        if (chartsGrid) chartsGrid.style.display = 'grid';
        
        document.getElementById('gauge-percent').innerText = `${summaryStats.current_month_progress}%`;
        document.getElementById('gauge-fill').style.transform = `rotate(${summaryStats.current_month_progress * 1.8}deg)`;
    }
}

async function fetchSettings() {
    const res = await fetch('/api/settings');
    settingsData = await res.json();
    
    // Update Connection Widget
    const widget = document.getElementById('connection-status-widget');
    const dot = widget.querySelector('.status-dot');
    const text = widget.querySelector('.status-text');
    
    if (settingsData.use_google_sheets) {
        widget.className = "connection-status online";
        text.innerText = "Sincronizado: Google Sheets";
    } else {
        widget.className = "connection-status offline";
        text.innerText = "Base Local (Simulada)";
    }
    
    // Populate form fields in Settings Tab
    document.getElementById('config-use-sheets').checked = settingsData.use_google_sheets;
    document.getElementById('config-spreadsheet-id').value = settingsData.spreadsheet_id || "";
    document.getElementById('config-credentials-json').value = settingsData.google_credentials_json || "";
    document.getElementById('config-late-day').value = settingsData.late_fee_day || 15;
    document.getElementById('config-late-amount').value = settingsData.late_fee_amount || 10.00;
    document.getElementById('config-currency').value = settingsData.currency || "$";
    document.getElementById('config-initial-bank-balance').value = settingsData.initial_bank_balance || 0.00;
    document.getElementById('config-default-aliquot').value = settingsData.default_aliquot_base || 70.00;
    document.getElementById('config-exonerate-directiva').checked = !!settingsData.exonerate_directiva;
    document.getElementById('config-receipt-recipient').value = settingsData.receipt_recipient_type || "inquilino";
    document.getElementById('config-app-mode').value = settingsData.app_mode || "produccion";
    
    const condoName = settingsData.condo_name || "Condominio El Mirador";
    document.getElementById('config-condo-name').value = condoName;
    const rucInput = document.getElementById('config-condo-ruc');
    if (rucInput) rucInput.value = settingsData.condo_ruc || "";
    const addrInput = document.getElementById('config-condo-address');
    if (addrInput) addrInput.value = settingsData.condo_address || "";
    
    const chatbotTitle = document.getElementById('chatbot-title');
    if (chatbotTitle) chatbotTitle.innerText = `Asistente Administrativo ${condoName}`;
    const chatbotIntro = document.getElementById('chatbot-intro-message');
    if (chatbotIntro) {
        chatbotIntro.innerHTML = `¡Hola! Soy el asistente virtual del <b>${condoName}</b>. Por favor, envíame los datos de tu comprobante de pago de alícuotas o expensas y adjunta tu comprobante físico (PDF o imagen) para registrarlo de inmediato.`;
    }
    
    // Notifications Settings
    document.getElementById('config-smtp-host').value = settingsData.smtp_host || "";
    document.getElementById('config-smtp-port').value = settingsData.smtp_port || 587;
    document.getElementById('config-smtp-user').value = settingsData.smtp_user || "";
    document.getElementById('config-smtp-password').value = settingsData.smtp_password || "";
    document.getElementById('config-smtp-from').value = settingsData.smtp_from || "";
    document.getElementById('config-twilio-sid').value = settingsData.twilio_sid || "";
    document.getElementById('config-twilio-token').value = settingsData.twilio_token || "";
    document.getElementById('config-twilio-from').value = settingsData.twilio_whatsapp_from || "";
    
    // WhatsApp Provider & Meta Cloud API settings
    const waProvider = settingsData.whatsapp_provider || "simulated";
    document.getElementById('config-whatsapp-provider').value = waProvider;
    document.getElementById('config-meta-token').value = settingsData.meta_wa_token || "";
    document.getElementById('config-meta-phone-id').value = settingsData.meta_wa_phone_number_id || "";
    document.getElementById('config-meta-waba-id').value = settingsData.meta_wa_business_account_id || "";
    document.getElementById('config-meta-verify-token').value = settingsData.meta_wa_verify_token || "";
    
    // Database Settings
    document.getElementById('config-db-type').value = settingsData.db_type || "sqlite";
    document.getElementById('config-db-host').value = settingsData.db_host || "";
    document.getElementById('config-db-port').value = settingsData.db_port || 5432;
    document.getElementById('config-db-user').value = settingsData.db_user || "";
    document.getElementById('config-db-password').value = settingsData.db_password || "";
    document.getElementById('config-db-name').value = settingsData.db_name || "";
    document.getElementById('config-db-custom-url').value = settingsData.db_custom_url || "";
    
    toggleWhatsappSettingsVisibility();
    toggleDatabaseSettingsVisibility();
}

async function fetchAliquots() {
    const res = await fetch('/api/aliquots');
    aliquotsData = await res.json();
    updateAliquotFilterOptions();
    renderAliquotsTable();
    renderRecentPayments();
}

async function fetchExpenses() {
    const res = await fetch('/api/expenses');
    expensesData = await res.json();
    renderExpensesTable();
    renderRecentExpenses();
}

async function fetchOtherIncomes() {
    const res = await fetch('/api/other-incomes');
    otherIncomesData = await res.json();
    renderOtherIncomesTable();
}

async function fetchBoardMembers() {
    const res = await fetch('/api/board-members');
    boardMembersData = await res.json();
    renderBoardMembersTable();
}

async function fetchDebtors() {
    const res = await fetch('/api/debtors');
    debtorsData = await res.json();
    renderDebtorsTable();
}

async function fetchAdditionalCharges() {
    const res = await fetch('/api/additional-charges');
    additionalChargesData = await res.json();
    renderAdditionalChargesTable();
    renderInitialDebtsTable();
}

async function fetchBankStatement() {
    const res = await fetch('/api/bank-statement');
    bankStatementData = await res.json();
    renderBankTransactionsTable();
}

// Update manual validation badge
function updateValidationBadge() {
    const validationCount = aliquotsData.filter(a => a.status === 'Validación Manual').length + 
                            additionalChargesData.filter(c => c.status === 'Validación Manual').length;
    
    const badge = document.getElementById('badge-validation-count');
    if (validationCount > 0) {
        badge.innerText = validationCount;
        badge.style.display = 'block';
    } else {
        badge.style.display = 'none';
    }
}

// Populate Dropdowns with apartments
function populateUnitDropdowns() {
    let uniqueUnits = [];
    if (unitsData && unitsData.length > 0) {
        uniqueUnits = unitsData.map(u => u.id).sort();
    } else {
        uniqueUnits = [...new Set(aliquotsData.map(a => a.unit))].sort();
    }
    
    const dropdowns = [
        document.getElementById('charge-unit'),
        document.getElementById('receipt-manual-unit'),
        document.getElementById('initial-debt-unit'),
        document.getElementById('board-unit')
    ];
    
    dropdowns.forEach(dropdown => {
        if (!dropdown) return;
        const currentVal = dropdown.value;
        dropdown.innerHTML = '<option value="">Selecciona Unidad</option>';
        uniqueUnits.forEach(unit => {
            const option = document.createElement('option');
            option.value = unit;
            option.innerText = `Depto ${unit}`;
            dropdown.appendChild(option);
        });
        dropdown.value = currentVal;
    });

    // Populate Reports Unit Dropdown
    const repUnitSelect = document.getElementById('rep-filter-unit');
    if (repUnitSelect) {
        const currentRepVal = repUnitSelect.value || '';
        repUnitSelect.innerHTML = '<option value="">Todos los departamentos</option>';
        uniqueUnits.forEach(unit => {
            const option = document.createElement('option');
            option.value = unit;
            option.innerText = `Depto ${unit}`;
            repUnitSelect.appendChild(option);
        });
        repUnitSelect.value = currentRepVal;
    }

    // Populate Emission Unit Dropdown (Masivo o Individual)
    const emitUnitSelect = document.getElementById('emit-aliquot-unit');
    if (emitUnitSelect) {
        const currentEmitVal = emitUnitSelect.value || 'all';
        emitUnitSelect.innerHTML = '<option value="all">🏢 Todos los Deptos (Masivo)</option>';
        uniqueUnits.forEach(unit => {
            const option = document.createElement('option');
            option.value = unit;
            option.innerText = `Depto ${unit}`;
            emitUnitSelect.appendChild(option);
        });
        emitUnitSelect.value = currentEmitVal;
        handleEmitUnitChange();
    }
}

// Handle emission unit dropdown change
function handleEmitUnitChange() {
    const emitUnitSelect = document.getElementById('emit-aliquot-unit');
    const amountInput = document.getElementById('emit-aliquot-amount');
    const btnText = document.getElementById('btn-emit-text');
    
    if (!emitUnitSelect) return;
    const val = emitUnitSelect.value;
    
    if (val === 'all' || !val) {
        if (amountInput) amountInput.style.display = 'none';
        if (btnText) btnText.innerText = 'Emitir Alícuotas (Masivo)';
    } else {
        if (amountInput) {
            amountInput.style.display = 'inline-block';
            // Pre-fill with unit's base aliquot if available
            const u = unitsData.find(unit => String(unit.id) === String(val));
            if (u && u.aliquot_base) {
                amountInput.value = u.aliquot_base;
            } else if (settingsData && settingsData.default_aliquot_base) {
                amountInput.value = settingsData.default_aliquot_base;
            }
        }
        if (btnText) btnText.innerText = `Generar Depto ${val}`;
    }
}

// Initialize and render Charts
function initCharts() {
    const currency = settingsData.currency || '$';
    
    // 1. Data calculation for cash flow (grouped by month)
    const monthsOrder = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
    const revenueByMonth = new Array(12).fill(0);
    const expensesByMonth = new Array(12).fill(0);
    
    // Helper to get month index from date string (YYYY-MM-DD) or month name
    function getMonthIndex(dateStr, defaultMonthName) {
        if (dateStr) {
            const parts = String(dateStr).split('T')[0].split('-');
            if (parts.length >= 2) {
                const monthNum = parseInt(parts[1], 10);
                if (monthNum >= 1 && monthNum <= 12) {
                    return monthNum - 1;
                }
            }
            const d = new Date(dateStr);
            if (!isNaN(d.getTime())) {
                return d.getMonth();
            }
        }
        if (defaultMonthName) {
            const idx = monthsOrder.indexOf(defaultMonthName);
            if (idx !== -1) return idx;
        }
        return -1;
    }

    // Revenue (from paid aliquots & partial abonos)
    aliquotsData.forEach(a => {
        if (a.status === 'Exonerado') return;
        
        let amt = 0;
        if (a.paid_amount && a.paid_amount > 0) {
            amt = a.paid_amount + (a.status === 'Pagado' ? (a.late_fee || 0) : 0);
        } else if (a.status === 'Pagado') {
            amt = (a.amount || 0) + (a.late_fee || 0);
        }
        
        if (amt > 0) {
            const idx = getMonthIndex(a.payment_date, a.month);
            if (idx !== -1) {
                revenueByMonth[idx] += amt;
            }
        }
    });

    // Revenue from additional charges & debt abonos
    additionalChargesData.forEach(c => {
        if (c.status === 'Exonerado') return;
        
        let amt = 0;
        if (c.paid_amount && c.paid_amount > 0) {
            amt = c.paid_amount;
        } else if (c.status === 'Pagado') {
            amt = (c.amount || 0);
        }
        
        if (amt > 0) {
            let idx = getMonthIndex(c.payment_date, null);
            if (idx === -1) idx = getMonthIndex(c.issue_date, null);
            if (idx !== -1) {
                revenueByMonth[idx] += amt;
            }
        }
    });

    // Revenue from other incomes (intereses, arriendos, varios)
    if (Array.isArray(otherIncomesData)) {
        otherIncomesData.forEach(o => {
            const amt = parseFloat(o.amount) || 0;
            if (amt > 0) {
                const idx = getMonthIndex(o.date, null);
                if (idx !== -1) {
                    revenueByMonth[idx] += amt;
                }
            }
        });
    }

    // Expenses (from expenses logs)
    expensesData.forEach(e => {
        const amt = parseFloat(e.amount) || 0;
        if (amt > 0) {
            const idx = getMonthIndex(e.date, null);
            if (idx !== -1) {
                expensesByMonth[idx] += amt;
            }
        }
    });

    // Render Cashflow Chart
    const ctxCashflow = document.getElementById('cashflowChart').getContext('2d');
    if (cashflowChart) cashflowChart.destroy();
    
    cashflowChart = new Chart(ctxCashflow, {
        type: 'bar',
        data: {
            labels: monthsOrder,
            datasets: [
                {
                    label: 'Ingresos Recaudados',
                    data: revenueByMonth,
                    backgroundColor: 'rgba(99, 102, 241, 0.7)',
                    borderColor: '#6366f1',
                    borderWidth: 1,
                    borderRadius: 4
                },
                {
                    label: 'Gastos Ejecutados',
                    data: expensesByMonth,
                    backgroundColor: 'rgba(239, 68, 68, 0.6)',
                    borderColor: '#ef4444',
                    borderWidth: 1,
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#f3f4f6', font: { family: 'Plus Jakarta Sans' } } }
            },
            scales: {
                x: { grid: { color: 'rgba(255, 255, 255, 0.05)' }, ticks: { color: '#9ca3af' } },
                y: { 
                    grid: { color: 'rgba(255, 255, 255, 0.05)' }, 
                    ticks: { color: '#9ca3af', callback: (val) => `${currency}${val}` } 
                }
            }
        }
    });

    // 2. Data calculation for expense categories
    const categories = {};
    expensesData.forEach(e => {
        categories[e.category] = (categories[e.category] || 0) + e.amount;
    });
    
    const catLabels = Object.keys(categories);
    const catValues = Object.values(categories);
    const catColors = [
        '#2563eb', // Blue
        '#10b981', // Green
        '#8b5cf6', // Violet
        '#f59e0b', // Amber
        '#ef4444', // Red
        '#64748b'  // Grey
    ];

    // Render Expenses Chart
    const ctxExpenses = document.getElementById('expensesChart').getContext('2d');
    if (expensesChart) expensesChart.destroy();
    
    expensesChart = new Chart(ctxExpenses, {
        type: 'doughnut',
        data: {
            labels: catLabels,
            datasets: [{
                data: catValues,
                backgroundColor: catColors.slice(0, catLabels.length),
                borderWidth: 1,
                borderColor: '#111827'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { 
                    position: 'right',
                    labels: { color: '#f3f4f6', font: { family: 'Plus Jakarta Sans', size: 10 } } 
                }
            }
        }
    });

    // Update gauge text details
    const currentMonth = "Junio";
    const currentYear = 2026;
    const curAliquots = aliquotsData.filter(a => a.month === currentMonth && a.year === currentYear);
    const paidCount = curAliquots.filter(a => a.status === 'Pagado').length;
    document.getElementById('gauge-subtitle').innerText = `${paidCount} de ${curAliquots.length} deptos pagados`;
}

// Render Recent Payments on Dashboard
function renderRecentPayments() {
    const list = document.getElementById('table-recent-payments').querySelector('tbody');
    list.innerHTML = "";
    
    let paidAliquots = aliquotsData
        .filter(a => a.status === 'Pagado' && a.payment_date)
        .sort((a,b) => new Date(b.payment_date) - new Date(a.payment_date));
        
    if (currentUser && (currentUser.role === 'owner' || currentUser.role === 'tenant')) {
        paidAliquots = paidAliquots.filter(a => String(a.unit) === String(currentUser.unit_id));
    }
    
    paidAliquots = paidAliquots.slice(0, 5);
        
    const currency = settingsData.currency || '$';

    if (paidAliquots.length === 0) {
        list.innerHTML = getTableEmptyStateHtml(5, 'inbox', 'No hay cobros registrados.');
        lucide.createIcons();
        return;
    }

    paidAliquots.forEach(a => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><strong>${a.unit}</strong></td>
            <td>${a.owner}</td>
            <td>${a.month} ${a.year}</td>
            <td>${currency}${(a.amount + a.late_fee).toFixed(2)}</td>
            <td><span class="status-badge paid">Pagado</span></td>
        `;
        list.appendChild(row);
    });
}

// Render Recent Expenses on Dashboard
function renderRecentExpenses() {
    const list = document.getElementById('table-recent-expenses').querySelector('tbody');
    list.innerHTML = "";
    
    const recentExp = expensesData.slice(0, 5);
    const currency = settingsData.currency || '$';

    if (recentExp.length === 0) {
        list.innerHTML = getTableEmptyStateHtml(4, 'wallet', 'No hay egresos registrados.');
        lucide.createIcons();
        return;
    }

    recentExp.forEach(e => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${e.date}</td>
            <td><span class="status-badge manual">${e.category}</span></td>
            <td>${e.description}</td>
            <td><strong>${currency}${e.amount.toFixed(2)}</strong></td>
        `;
        list.appendChild(row);
    });
}

// Update aliquot filter month and year options dynamically from available aliquots
function updateAliquotFilterOptions() {
    const monthSelect = document.getElementById('filter-aliquot-month');
    const yearSelect = document.getElementById('filter-aliquot-year');
    if (!monthSelect || !yearSelect) return;
    
    const selectedMonth = monthSelect.value;
    const selectedYear = yearSelect.value;
    
    // Get unique months and years from aliquotsData
    const months = [...new Set(aliquotsData.map(a => a.month).filter(Boolean))];
    const years = [...new Set(aliquotsData.map(a => a.year).filter(Boolean))];
    
    // Sort months correctly (Spanish month order)
    const monthOrder = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
    months.sort((a, b) => monthOrder.indexOf(a) - monthOrder.indexOf(b));
    
    // Sort years ascending
    years.sort((a, b) => a - b);
    
    // Populate months
    monthSelect.innerHTML = '<option value="">Todos los Meses</option>';
    months.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        monthSelect.appendChild(opt);
    });
    
    // Populate years
    yearSelect.innerHTML = '<option value="">Todos los Años</option>';
    years.forEach(y => {
        const opt = document.createElement('option');
        opt.value = y;
        opt.textContent = y;
        yearSelect.appendChild(opt);
    });
    
    // Restore previous selection if still available
    if (months.includes(selectedMonth)) {
        monthSelect.value = selectedMonth;
    }
    if (years.map(String).includes(selectedYear)) {
        yearSelect.value = selectedYear;
    }
}

// Render detailed Aliquots Tab table
function renderAliquotsTable() {
    const tableBody = document.getElementById('table-aliquots').querySelector('tbody');
    tableBody.innerHTML = "";
    
    const searchVal = document.getElementById('filter-aliquot-search').value.toLowerCase();
    const monthVal = document.getElementById('filter-aliquot-month').value;
    const yearVal = document.getElementById('filter-aliquot-year').value;
    const statusVal = document.getElementById('filter-aliquot-status').value;
    const currency = settingsData.currency || '$';

    let filtered = aliquotsData;
    
    if (searchVal) {
        filtered = filtered.filter(a => a.unit.toLowerCase().includes(searchVal) || a.owner.toLowerCase().includes(searchVal));
    }
    if (monthVal) {
        filtered = filtered.filter(a => a.month === monthVal);
    }
    if (yearVal) {
        filtered = filtered.filter(a => String(a.year) === yearVal);
    }
    if (statusVal) {
        filtered = filtered.filter(a => a.status === statusVal);
    }

    // Sort by unit first, then year, then month index
    filtered.sort((a,b) => {
        if (a.unit !== b.unit) return a.unit.localeCompare(b.unit);
        if (a.year !== b.year) return b.year - a.year;
        return a.month.localeCompare(b.month);
    });

    const totalItems = filtered.length;
    const totalPages = Math.ceil(totalItems / aliquotsLimit) || 1;
    
    // Adjust current page if it is out of bounds
    if (aliquotsPage > totalPages) {
        aliquotsPage = totalPages;
    }

    if (filtered.length === 0) {
        tableBody.innerHTML = getTableEmptyStateHtml(11, 'receipt', 'No se encontraron alícuotas', 'Modifica los filtros de búsqueda para reintentar.');
        const pagContainer = document.getElementById('pagination-aliquots');
        if (pagContainer) pagContainer.innerHTML = "";
        lucide.createIcons();
        return;
    }

    const startIdx = (aliquotsPage - 1) * aliquotsLimit;
    const endIdx = startIdx + aliquotsLimit;
    const paginatedItems = filtered.slice(startIdx, endIdx);

    paginatedItems.forEach(a => {
        const row = document.createElement('tr');
        
        let statusClass = "pending";
        let statusText = "Pendiente";
        if (a.status === 'Pagado') {
            statusClass = "paid";
            statusText = "Pagado";
        } else if (a.status === 'Exonerado') {
            statusClass = "exonerated";
            statusText = "Exonerado";
        } else if (a.status === 'Validación Manual') {
            statusClass = "manual";
            statusText = "Val. Manual";
        }
        
        // Actions buttons depending on state
        let actions = '<div class="aliquot-actions">';
        if (a.status === 'Pagado') {
            actions += `<a href="/api/receipts/aliquot/${a.id}/pdf" target="_blank" class="btn btn-secondary-sm" title="Ver / Imprimir Recibo Oficial"><i data-lucide="printer" style="width:12px;height:12px;"></i> Recibo</a> 
                        <button class="btn btn-secondary-sm" onclick="resendReceipt('${a.id}', 'aliquot')" title="Reenviar por Mail y WhatsApp"><i data-lucide="send" style="width:12px;height:12px;"></i> Reenviar</button>
                        <button class="btn btn-secondary-sm" onclick="openEditAliquotModal('${a.id}', '${a.unit}', '${a.month} ${a.year}', ${a.amount}, '${(a.reference || '').replace(/'/g, "\\'")}', '${a.payment_date || ''}')" title="Editar Referencia / Alícuota"><i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar</button>
                        <button class="btn btn-secondary-sm text-red" onclick="confirmUndoPayment('${a.id}', 'aliquot')" title="Eliminar/Deshacer Pago"><i data-lucide="trash-2" style="width:12px;height:12px;"></i> Deshacer</button>`;
        } else if (a.status === 'Exonerado') {
            actions += `<span style="font-size:12px;color:#60a5fa;display:inline-flex;align-items:center;gap:4px;font-weight:500;"><i data-lucide="shield-check" style="width:14px;height:14px;"></i> Directiva</span>`;
        } else if (a.status === 'Validación Manual') {
            actions += `<button class="btn btn-primary" onclick="openManualValidationModal('${a.id}', 'aliquot', '${a.unit}', '${a.month} ${a.year}', ${a.amount})"><i data-lucide="check" style="width:12px;height:12px;"></i> Validar</button>
                        <button class="btn btn-secondary-sm" onclick="openEditAliquotModal('${a.id}', '${a.unit}', '${a.month} ${a.year}', ${a.amount}, '${(a.reference || '').replace(/'/g, "\\'")}', '${a.payment_date || ''}')" title="Editar Referencia / Alícuota"><i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar</button>
                        <button class="btn btn-secondary-sm" onclick="triggerReceiptUpload('${a.id}')" title="Subir Comprobante Físico"><i data-lucide="upload" style="width:12px;height:12px;"></i> Subir</button>
                        <button class="btn btn-secondary-sm text-red" onclick="confirmUndoPayment('${a.id}', 'aliquot')" title="Rechazar/Deshacer"><i data-lucide="trash-2" style="width:12px;height:12px;"></i> Deshacer</button>`;
        } else {
            actions += `<button class="btn btn-secondary-sm" onclick="openManualValidationModal('${a.id}', 'aliquot', '${a.unit}', '${a.month} ${a.year}', ${a.amount})">Registrar</button>
                        <button class="btn btn-secondary-sm" onclick="openEditAliquotModal('${a.id}', '${a.unit}', '${a.month} ${a.year}', ${a.amount}, '${(a.reference || '').replace(/'/g, "\\'")}', '${a.payment_date || ''}')" title="Editar Referencia / Alícuota"><i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar</button>
                        <button class="btn btn-secondary-sm" onclick="triggerReceiptUpload('${a.id}')" title="Subir Comprobante Físico"><i data-lucide="upload" style="width:12px;height:12px;"></i> Subir</button>`;
        }

        if (a.comprobante_url) {
            actions += `<a href="${a.comprobante_url}" target="_blank" class="btn btn-secondary-sm" title="Ver Comprobante Original" style="border-color: rgba(99, 102, 241, 0.3); background: rgba(99, 102, 241, 0.05); color: #c7d2fe;"><i data-lucide="image" style="width:12px;height:12px;"></i> Foto</a>`;
        }
        
        if (currentUser && currentUser.role === 'superadmin') {
            actions += `<button class="btn btn-secondary-sm text-red" onclick="deleteAliquot('${a.id}', '${a.unit}', '${a.month}', ${a.year})" title="Eliminar Alícuota (Solo Superadministrador)"><i data-lucide="trash" style="width:12px;height:12px;"></i> Eliminar</button>`;
        }
        actions += '</div>';

        const amountEditBtn = (a.status !== 'Pagado' && a.status !== 'Exonerado') ? `<button class="btn-icon-subtle" onclick="openEditAliquotModal('${a.id}', '${a.unit}', '${a.month} ${a.year}', ${a.amount}, '${(a.reference || '').replace(/'/g, "\\'")}', '${a.payment_date || ''}')" title="Editar Valor de Alícuota" style="background:transparent;border:none;color:var(--text-muted);cursor:pointer;margin-left:4px;vertical-align:middle;"><i data-lucide="edit-2" style="width:12px;height:12px;"></i></button>` : '';

        let aliquotAmtDisplay = `${currency}${a.amount.toFixed(2)}${amountEditBtn}`;
        if (a.paid_amount && a.paid_amount > 0 && a.status !== 'Pagado' && a.status !== 'Exonerado') {
            aliquotAmtDisplay = `${currency}${a.amount.toFixed(2)}${amountEditBtn}<br/><small style="color:#10b981;font-weight:600;font-size:11px;">(Abonado: ${currency}${a.paid_amount.toFixed(2)})</small>`;
        }

        const refEditBtn = `<button class="btn-icon-subtle" onclick="openEditAliquotModal('${a.id}', '${a.unit}', '${a.month} ${a.year}', ${a.amount}, '${(a.reference || '').replace(/'/g, "\\'")}', '${a.payment_date || ''}')" title="Editar Referencia Bancaria" style="background:transparent;border:none;color:var(--text-muted);cursor:pointer;margin-left:4px;vertical-align:middle;"><i data-lucide="edit-3" style="width:12px;height:12px;"></i></button>`;
        const refDisplay = a.reference ? `<code>${a.reference}</code>${refEditBtn}` : `<span class="text-muted">-</span>${refEditBtn}`;

        row.innerHTML = `
            <td><small>${a.id}</small></td>
            <td><strong>${a.unit}</strong></td>
            <td>${a.owner}</td>
            <td>${a.month} ${a.year}</td>
            <td>${aliquotAmtDisplay}</td>
            <td>${currency}${a.late_fee.toFixed(2)}</td>
            <td><strong>${currency}${(a.amount + a.late_fee).toFixed(2)}</strong></td>
            <td>${a.payment_date || '-'}</td>
            <td>${refDisplay}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            <td>${actions}</td>
        `;
        tableBody.appendChild(row);
    });

    renderPagination('pagination-aliquots', aliquotsPage, totalItems, aliquotsLimit, (page) => {
        aliquotsPage = page;
        renderAliquotsTable();
    });
    lucide.createIcons();
}

// Delete Aliquot (Superadmin only)
async function deleteAliquot(aliquotId, unit, month, year) {
    if (!currentUser || currentUser.role !== 'superadmin') {
        showToast("Permiso denegado. Solo el Superadministrador puede eliminar alícuotas.", "error");
        return;
    }

    const confirmMsg = `¿Estás seguro de que deseas eliminar permanentemente la alícuota del Depto ${unit} correspondiente a ${month} ${year}?\n\nEsta acción es irreversible y solo puede ser ejecutada por el Superadministrador.`;
    if (!confirm(confirmMsg)) {
        return;
    }

    try {
        const res = await fetch(`/api/aliquots/${aliquotId}`, {
            method: 'DELETE',
            headers: {
                'X-User-Role': currentUser.role
            }
        });

        if (res.ok) {
            const data = await res.json();
            showToast(data.message || "Alícuota eliminada exitosamente.");
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al eliminar la alícuota.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación con el servidor.", "error");
    }
}

// Emit aliquots (bulk or individual per unit)
async function emitAliquots() {
    const unitSelect = document.getElementById('emit-aliquot-unit');
    const unit_id = unitSelect ? unitSelect.value : 'all';
    const month = document.getElementById('emit-aliquot-month').value;
    const year = parseInt(document.getElementById('emit-aliquot-year').value);
    const amountInput = document.getElementById('emit-aliquot-amount');
    const amount = (amountInput && amountInput.value && parseFloat(amountInput.value) >= 0) ? parseFloat(amountInput.value) : null;
    
    const isIndividual = unit_id && unit_id !== 'all';
    const label = isIndividual ? `alícuota para el Depto ${unit_id}` : `alícuotas masivas`;
    
    showToast(`Generando ${label} (${month} ${year})...`, 'warning');
    
    try {
        const payload = {
            month,
            year,
            unit_id: isIndividual ? unit_id : null,
            amount: amount
        };
        
        const res = await fetch('/api/aliquots/emit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            const data = await res.json();
            showToast(data.message || `Emisión exitosa.`);
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al emitir alícuota.", "error");
        }
    } catch (err) {
        showToast("Error de conexión con el servidor.", "error");
    }
}

// Row-level receipt uploading triggers
function triggerReceiptUpload(id) {
    uploadingAliquotId = id;
    document.getElementById('aliquot-file-input').click();
}

// Handle file input change event for aliquot
async function handleAliquotFileChange(event) {
    const file = event.target.files[0];
    if (!file || !uploadingAliquotId) return;
    
    const id = uploadingAliquotId;
    uploadingAliquotId = null; // reset
    event.target.value = ''; // reset input
    
    await uploadReceiptForAliquot(id, file);
}

// Upload receipt file for aliquot
async function uploadReceiptForAliquot(id, file) {
    showToast("Subiendo y validando comprobante...", "warning");
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const res = await fetch(`/api/aliquots/${id}/upload-receipt`, {
            method: 'POST',
            body: formData
        });
        
        if (res.ok) {
            const result = await res.json();
            if (result.status === 'success') {
                showToast("¡Comprobante procesado y conciliado en banco!");
                if (result.receipt_url) {
                    window.open(result.receipt_url, '_blank');
                }
            } else if (result.status === 'manual_validation') {
                showToast("Comprobante registrado: Referencia no coincide (Validación Manual).", "warning");
            } else if (result.status === 'error') {
                showToast(result.message || "Error al validar el comprobante.", "error");
            } else {
                showToast("Error inesperado en el servidor.", "error");
            }
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error de red al procesar comprobante.", "error");
        }
    } catch (err) {
        showToast("Error de conexión al servidor.", "error");
    }
}

// Render detailed Expenses history
function renderExpensesTable() {
    const tbody = document.getElementById('table-expenses').querySelector('tbody');
    tbody.innerHTML = "";
    const currency = settingsData.currency || '$';

    const totalItems = expensesData.length;
    const totalPages = Math.ceil(totalItems / expensesLimit) || 1;
    
    if (expensesPage > totalPages) {
        expensesPage = totalPages;
    }

    if (expensesData.length === 0) {
        tbody.innerHTML = getTableEmptyStateHtml(4, 'wallet', 'No hay egresos registrados.');
        const pagContainer = document.getElementById('pagination-expenses');
        if (pagContainer) pagContainer.innerHTML = "";
        lucide.createIcons();
        return;
    }

    const startIdx = (expensesPage - 1) * expensesLimit;
    const endIdx = startIdx + expensesLimit;
    const paginatedItems = expensesData.slice(startIdx, endIdx);

    paginatedItems.forEach(e => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${e.date}</td>
            <td><span class="status-badge manual">${e.category}</span></td>
            <td>${e.description}</td>
            <td><strong>${currency}${e.amount.toFixed(2)}</strong></td>
            <td>
                <div style="display:flex; gap:6px;">
                    <button class="btn-action-small" onclick="editExpense('${e.id}')" title="Editar Gasto" style="background:var(--primary); color:white; border-color:var(--primary); padding:4px 8px; display:flex; align-items:center; gap:4px;">
                        <i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar
                    </button>
                    <button class="btn-action-small btn-undo" onclick="confirmDeleteExpense('${e.id}')" title="Eliminar Gasto" style="padding:4px 8px; display:flex; align-items:center; gap:4px;">
                        <i data-lucide="trash-2" style="width:12px;height:12px;"></i> Eliminar
                    </button>
                </div>
            </td>
        `;
        tbody.appendChild(row);
    });

    renderPagination('pagination-expenses', expensesPage, totalItems, expensesLimit, (page) => {
        expensesPage = page;
        renderExpensesTable();
    });
    lucide.createIcons();
}

function renderOtherIncomesTable() {
    const tbody = document.getElementById('table-other-incomes').querySelector('tbody');
    tbody.innerHTML = "";
    const currency = settingsData.currency || '$';

    const totalItems = otherIncomesData.length;
    const totalPages = Math.ceil(totalItems / otherIncomesLimit) || 1;
    
    if (otherIncomesPage > totalPages) {
        otherIncomesPage = totalPages;
    }

    if (otherIncomesData.length === 0) {
        tbody.innerHTML = getTableEmptyStateHtml(5, 'coins', 'No hay otros ingresos registrados.');
        const pagContainer = document.getElementById('pagination-other-incomes');
        if (pagContainer) pagContainer.innerHTML = "";
        lucide.createIcons();
        return;
    }

    const startIdx = (otherIncomesPage - 1) * otherIncomesLimit;
    const endIdx = startIdx + otherIncomesLimit;
    const paginatedItems = otherIncomesData.slice(startIdx, endIdx);

    paginatedItems.forEach(o => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${o.date}</td>
            <td>${o.concept}</td>
            <td><code>${o.reference || 'N/A'}</code></td>
            <td><strong>${currency}${o.amount.toFixed(2)}</strong></td>
            <td>
                <div style="display:flex; gap:6px;">
                    <button class="btn-action-small" onclick="editOtherIncome(${o.id})" title="Editar Ingreso" style="background:var(--primary); color:white; border-color:var(--primary); padding:4px 8px; display:flex; align-items:center; gap:4px;">
                        <i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar
                    </button>
                    <button class="btn-action-small btn-undo" onclick="confirmDeleteOtherIncome(${o.id})" title="Eliminar/Deshacer Ingreso" style="padding:4px 8px; display:flex; align-items:center; gap:4px;">
                        <i data-lucide="trash-2" style="width:12px;height:12px;"></i> Eliminar
                    </button>
                </div>
            </td>
        `;
        tbody.appendChild(row);
    });

    renderPagination('pagination-other-incomes', otherIncomesPage, totalItems, otherIncomesLimit, (page) => {
        otherIncomesPage = page;
        renderOtherIncomesTable();
    });
    lucide.createIcons();
}

async function confirmDeleteOtherIncome(id) {
    if (!confirm("¿Está seguro de que desea eliminar este ingreso? Si estaba conciliado con una transacción bancaria, esta volverá a estar pendiente.")) {
        return;
    }
    try {
        const res = await fetch(`/api/other-incomes/${id}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            showToast("Ingreso eliminado con éxito.");
            await loadAllData();
        } else {
            const data = await res.json();
            showToast(data.detail || "Error al eliminar el ingreso", "error");
        }
    } catch (e) {
        console.error(e);
        showToast("Error de conexión", "error");
    }
}

function editOtherIncome(id) {
    const o = otherIncomesData.find(x => x.id === id);
    if (!o) return;
    
    editingOtherIncomeId = id;
    const titleEl = document.getElementById('modal-other-income-title');
    const btnEl = document.getElementById('btn-submit-other-income');
    if (titleEl) titleEl.innerText = "Editar Otros Ingresos";
    if (btnEl) btnEl.innerText = "Actualizar Ingreso";
    
    document.getElementById('other-income-date').value = o.date;
    document.getElementById('other-income-concept').value = o.concept;
    document.getElementById('other-income-amount').value = o.amount;
    document.getElementById('other-income-reference').value = o.reference || "";
    
    openModal('modal-add-other-income');
}

function editExpense(id) {
    const exp = expensesData.find(x => x.id === id);
    if (!exp) return;
    
    editingExpenseId = id;
    const titleEl = document.getElementById('modal-expense-title');
    const btnEl = document.getElementById('btn-submit-expense');
    if (titleEl) titleEl.innerText = "Editar Gasto";
    if (btnEl) btnEl.innerText = "Actualizar Gasto";
    
    document.getElementById('expense-date').value = exp.date;
    document.getElementById('expense-category').value = exp.category;
    document.getElementById('expense-amount').value = exp.amount;
    document.getElementById('expense-description').value = exp.description;
    
    openModal('modal-add-expense');
}

async function confirmDeleteExpense(id) {
    if (!confirm("¿Está seguro de que desea eliminar este egreso/gasto?")) {
        return;
    }
    try {
        const res = await fetch(`/api/expenses/${id}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            showToast("Egreso eliminado con éxito.");
            await loadAllData();
        } else {
            const data = await res.json();
            showToast(data.detail || "Error al eliminar el egreso", "error");
        }
    } catch (e) {
        console.error(e);
        showToast("Error de conexión", "error");
    }
}

function renderBoardMembersTable() {
    const tbody = document.getElementById('table-board-members').querySelector('tbody');
    tbody.innerHTML = "";
    
    if (boardMembersData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="text-center text-muted">No hay miembros de la directiva registrados.</td></tr>`;
        return;
    }
    
    boardMembersData.forEach(m => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><strong>Depto ${m.unit_id}</strong></td>
            <td>${m.name}</td>
            <td><span class="status-badge manual">${m.role}</span></td>
            <td>
                <button class="btn-action-small btn-undo" onclick="confirmDeleteBoardMember(${m.id})" title="Eliminar Miembro">
                    <i data-lucide="trash-2"></i> Eliminar
                </button>
            </td>
        `;
        tbody.appendChild(row);
    });
    lucide.createIcons();
}

async function confirmDeleteBoardMember(id) {
    if (!confirm("¿Está seguro de que desea eliminar a este miembro de la directiva?")) {
        return;
    }
    try {
        const res = await fetch(`/api/board-members/${id}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            showToast("Miembro de la directiva eliminado con éxito.");
            await loadAllData();
        } else {
            const data = await res.json();
            showToast(data.detail || "Error al eliminar el miembro", "error");
        }
    } catch (e) {
        console.error(e);
        showToast("Error de conexión", "error");
    }
}

async function handleAddBoardMember(e) {
    e.preventDefault();
    const unit_id = document.getElementById('board-unit').value;
    const name = document.getElementById('board-name').value;
    const role = document.getElementById('board-role').value;

    try {
        const res = await fetch('/api/board-members', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ unit_id, name, role })
        });
        
        if (res.ok) {
            showToast("Miembro de la directiva registrado correctamente.");
            document.getElementById('form-add-board-member').reset();
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al registrar miembro.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

// Render Debtors table
function renderDebtorsTable() {
    const tbody = document.getElementById('table-debtors').querySelector('tbody');
    tbody.innerHTML = "";
    const currency = settingsData.currency || '$';

    const moraData = debtorsData.filter(d => d.total_debt > 0);

    if (moraData.length === 0) {
        tbody.innerHTML = getTableEmptyStateHtml(6, 'check-circle-2', '¡Felicitaciones!', 'No existen deudores en el edificio.');
        lucide.createIcons();
        return;
    }

    moraData.forEach(d => {
        const row = document.createElement('tr');
        
        // List unpaid details as small items
        const detailsList = d.details.map(item => `<li><small>${item}</small></li>`).join('');
        
        // Prefilled warning message
        const condoName = settingsData.condo_name || 'Condominio El Mirador';
        const warningMessage = `Estimado(a) ${d.owner}, de la administración del ${condoName} le escribimos para recordarle que tiene un saldo pendiente de ${currency}${d.total_debt.toFixed(2)} correspondiente a: ${d.details.join(', ')}. Agradecemos su puntualidad y pronto pago.`;
        const encodedMsg = encodeURIComponent(warningMessage);
        
        // WhatsApp button logic
        const whatsappBtn = `<a href="https://web.whatsapp.com/send?text=${encodedMsg}" target="_blank" class="btn btn-secondary-sm text-red"><i data-lucide="bell" style="width:12px;height:12px;"></i> Cobrar</a>`;

        row.innerHTML = `
            <td><strong>Depto ${d.unit}</strong></td>
            <td>${d.owner}</td>
            <td class="text-center"><span class="badge" style="position:relative; right:auto;">${d.unpaid_aliquots_count}</span></td>
            <td>
                <ul style="list-style-type:none; padding-left:0;">
                    ${detailsList}
                </ul>
            </td>
            <td><strong class="text-red">${currency}${d.total_debt.toFixed(2)}</strong></td>
            <td>${whatsappBtn}</td>
        `;
        tbody.appendChild(row);
    });
    lucide.createIcons();
}

// Render Additional Charges Log table
function renderAdditionalChargesTable() {
    const tbody = document.getElementById('table-additional-charges').querySelector('tbody');
    tbody.innerHTML = "";
    const currency = settingsData.currency || '$';

    if (additionalChargesData.length === 0) {
        tbody.innerHTML = getTableEmptyStateHtml(9, 'alert-triangle', 'No hay cobros extraordinarios registrados.');
        lucide.createIcons();
        return;
    }

    additionalChargesData.forEach(c => {
        const row = document.createElement('tr');
        
        const hasReceipt = c.reference && c.reference.trim() !== "";
        let statusClass = "pending";
        let statusText = "Pendiente";
        let action = '<div class="aliquot-actions" style="display:flex; gap:6px; align-items:center;">';
        if (c.status === 'Pagado') {
            statusClass = "paid";
            statusText = "Pagado";
            action += `<a href="/api/receipts/charge/${c.id}/pdf" target="_blank" class="btn btn-secondary-sm" title="Ver / Imprimir Recibo Oficial"><i data-lucide="printer" style="width:12px;height:12px;"></i> Recibo</a>
                       <button class="btn btn-secondary-sm" onclick="resendReceipt('${c.id}', 'charge')" title="Reenviar por Mail y WhatsApp"><i data-lucide="send" style="width:12px;height:12px;"></i> Reenviar</button>
                       <button class="btn btn-secondary-sm" onclick="editInitialDebt('${c.id}')" title="Editar Monto / Referencia"><i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar</button>
                       <button class="btn btn-secondary-sm text-red" onclick="confirmUndoPayment('${c.id}', 'charge')" title="Eliminar/Deshacer Pago"><i data-lucide="trash-2" style="width:12px;height:12px;"></i> Deshacer</button>`;
        } else if (c.status === 'Validación Manual') {
            statusClass = "manual";
            statusText = "Val. Manual";
            action += `<button class="btn btn-primary" onclick="openManualValidationModal('${c.id}', 'charge', '${c.unit}', '${c.type}: ${c.description}', ${c.amount})"><i data-lucide="check" style="width:12px;height:12px;"></i> Validar</button>
                       <button class="btn btn-secondary-sm" onclick="editInitialDebt('${c.id}')" title="Editar Monto / Referencia"><i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar</button>`;
            if (hasReceipt) {
                action += `<a href="/api/receipts/charge/${c.id}/pdf" target="_blank" class="btn btn-secondary-sm" title="Ver / Imprimir Recibo"><i data-lucide="printer" style="width:12px;height:12px;"></i> Recibo</a>
                           <button class="btn btn-secondary-sm" onclick="resendReceipt('${c.id}', 'charge')" title="Reenviar"><i data-lucide="send" style="width:12px;height:12px;"></i> Reenviar</button>`;
            }
            action += `<button class="btn btn-secondary-sm text-red" onclick="confirmUndoPayment('${c.id}', 'charge')" title="Rechazar/Deshacer"><i data-lucide="trash-2" style="width:12px;height:12px;"></i> Deshacer</button>`;
        } else {
            action += `<button class="btn btn-secondary-sm" onclick="openManualValidationModal('${c.id}', 'charge', '${c.unit}', '${c.type}: ${c.description}', ${c.amount})">Cobrar</button>
                       <button class="btn btn-secondary-sm" onclick="editInitialDebt('${c.id}')" title="Editar Monto / Referencia"><i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar</button>`;
            if (hasReceipt) {
                action += `<a href="/api/receipts/charge/${c.id}/pdf" target="_blank" class="btn btn-secondary-sm" title="Ver / Imprimir Recibo"><i data-lucide="printer" style="width:12px;height:12px;"></i> Recibo</a>
                           <button class="btn btn-secondary-sm" onclick="resendReceipt('${c.id}', 'charge')" title="Reenviar"><i data-lucide="send" style="width:12px;height:12px;"></i> Reenviar</button>
                           <button class="btn btn-secondary-sm text-red" onclick="confirmUndoPayment('${c.id}', 'charge')" title="Rechazar/Deshacer"><i data-lucide="trash-2" style="width:12px;height:12px;"></i> Deshacer</button>`;
            }
        }
        action += '</div>';

        const typeBadge = c.type === 'Multa' ? 'text-red' : 'text-purple';

        let chargeAmtDisplay = `<strong>${currency}${c.amount.toFixed(2)}</strong>`;
        if (c.paid_amount && c.paid_amount > 0 && c.status !== 'Pagado' && c.status !== 'Exonerado') {
            chargeAmtDisplay = `<strong>${currency}${c.amount.toFixed(2)}</strong><br/><small style="color:#10b981;font-weight:600;font-size:11px;">(Abonado: ${currency}${c.paid_amount.toFixed(2)})</small>`;
        }

        row.innerHTML = `
            <td><small>${c.id}</small></td>
            <td><strong>${c.unit}</strong></td>
            <td>${c.owner}</td>
            <td><span class="${typeBadge}"><b>${c.type}</b></span></td>
            <td><small>${c.description}</small></td>
            <td>${chargeAmtDisplay}</td>
            <td>${c.issue_date}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            <td>${action}</td>
        `;
        tbody.appendChild(row);
    });
    lucide.createIcons();
}

// Render Bank statement table
function renderBankTransactionsTable() {
    const tbody = document.getElementById('table-bank-transactions').querySelector('tbody');
    tbody.innerHTML = "";
    const currency = settingsData.currency || '$';

    const totalItems = bankStatementData.length;
    const totalPages = Math.ceil(totalItems / bankLimit) || 1;
    
    if (bankPage > totalPages) {
        bankPage = totalPages;
    }

    if (bankStatementData.length === 0) {
        tbody.innerHTML = getTableEmptyStateHtml(5, 'landmark', 'No hay transacciones registradas en el banco.', 'Carga un archivo de extracto bancario para empezar.');
        const pagContainer = document.getElementById('pagination-bank');
        if (pagContainer) pagContainer.innerHTML = "";
        lucide.createIcons();
        return;
    }

    const startIdx = (bankPage - 1) * bankLimit;
    const endIdx = startIdx + bankLimit;
    const paginatedItems = bankStatementData.slice(startIdx, endIdx);

    paginatedItems.forEach(b => {
        const row = document.createElement('tr');
        const statusClass = b.reconciled ? "paid" : "unreconciled";
        const statusText = b.reconciled ? "Conciliado" : "Pendiente";
        const safeDetail = (b.detail || '').replace(/'/g, "\\'");
        const safeRef = (b.reference || '').replace(/'/g, "\\'");
        
        const actionHtml = `
            <div style="display:flex; gap:5px; align-items:center;">
                ${!b.reconciled ? `
                <button class="btn btn-secondary-sm" onclick="openReconcileOtherIncomeModal('${safeRef}', '${b.date}', ${b.amount}, '${safeDetail}')" title="Conciliar como Otro Ingreso">
                    <i data-lucide="coins" style="width:12px;height:12px;"></i> Conciliar
                </button>` : ''}
                <button class="btn btn-secondary-sm" onclick="openEditBankTransactionModal('${safeRef}', '${b.date}', ${b.amount}, '${safeDetail}')" title="Editar Referencia / Detalle">
                    <i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar
                </button>
                ${!b.reconciled ? `
                <button class="btn btn-danger-sm" onclick="deleteBankTransaction('${safeRef}')" style="background-color: var(--danger-color); color: white;" title="Eliminar Transacción">
                    <i data-lucide="trash-2" style="width:12px;height:12px;"></i>
                </button>` : ''}
            </div>
        `;

        const refEditBtn = `<button class="btn-icon-subtle" onclick="openEditBankTransactionModal('${safeRef}', '${b.date}', ${b.amount}, '${safeDetail}')" title="Editar Referencia Bancaria" style="background:transparent;border:none;color:var(--text-muted);cursor:pointer;margin-left:4px;vertical-align:middle;"><i data-lucide="edit-3" style="width:12px;height:12px;"></i></button>`;

        row.innerHTML = `
            <td>${b.date}</td>
            <td><code>${b.reference}</code>${refEditBtn}</td>
            <td><strong>${currency}${b.amount.toFixed(2)}</strong></td>
            <td>${b.detail}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            <td>${actionHtml}</td>
        `;
        tbody.appendChild(row);
    });

    renderPagination('pagination-bank', bankPage, totalItems, bankLimit, (page) => {
        bankPage = page;
        renderBankTransactionsTable();
    });
    lucide.createIcons();
}

// Delete bank transaction
async function deleteBankTransaction(reference) {
    if (!confirm("¿Está seguro de que desea eliminar este registro del estado de cuenta?")) {
        return;
    }
    try {
        const res = await fetch(`/api/bank-statement/${encodeURIComponent(reference)}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            showToast("Transacción bancaria eliminada.");
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al eliminar la transacción.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

// Handle Forms
async function handleAddExpense(e) {
    e.preventDefault();
    const date = document.getElementById('expense-date').value;
    const category = document.getElementById('expense-category').value;
    const amount = parseFloat(document.getElementById('expense-amount').value);
    const description = document.getElementById('expense-description').value;

    const url = editingExpenseId ? `/api/expenses/${editingExpenseId}` : '/api/expenses';
    const method = editingExpenseId ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date, category, description, amount })
        });
        
        if (res.ok) {
            showToast(editingExpenseId ? "Gasto actualizado correctamente." : "Gasto registrado correctamente.");
            closeModal('modal-add-expense');
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al guardar el gasto.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

function openReconcileOtherIncomeModal(ref, date, amount, detail) {
    document.getElementById('other-income-reference').value = ref;
    // Set date (if it's in DD/MM/YYYY, convert to YYYY-MM-DD for date input)
    let formattedDate = date;
    if (date.includes("/")) {
        const parts = date.split("/");
        if (parts.length === 3) {
            // Check if parts[0] is year or day
            if (parts[2].length === 4) {
                formattedDate = `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
            }
        }
    }
    document.getElementById('other-income-date').value = formattedDate;
    document.getElementById('other-income-amount').value = amount;
    document.getElementById('other-income-concept').value = `Otros Ingresos: ${detail}`;
    openModal('modal-add-other-income');
}

async function handleAddOtherIncome(e) {
    e.preventDefault();
    const date = document.getElementById('other-income-date').value;
    const concept = document.getElementById('other-income-concept').value;
    const amount = parseFloat(document.getElementById('other-income-amount').value);
    const reference = document.getElementById('other-income-reference').value;

    const url = editingOtherIncomeId ? `/api/other-incomes/${editingOtherIncomeId}` : '/api/other-incomes';
    const method = editingOtherIncomeId ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date, concept, amount, reference })
        });
        
        if (res.ok) {
            showToast(editingOtherIncomeId ? "Ingreso actualizado correctamente." : "Ingreso registrado correctamente.");
            closeModal('modal-add-other-income');
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al guardar el ingreso.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

async function handleAddCharge(e) {
    e.preventDefault();
    const unit = document.getElementById('charge-unit').value;
    const type = document.getElementById('charge-type').value;
    const amount = parseFloat(document.getElementById('charge-amount').value);
    const description = document.getElementById('charge-description').value;

    if (!unit) {
        showToast("Por favor selecciona una unidad.", "warning");
        return;
    }

    try {
        const res = await fetch('/api/additional-charges', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ unit, type, amount, description })
        });
        
        if (res.ok) {
            showToast("Cargo extraordinario emitido correctamente.");
            document.getElementById('form-add-charge').reset();
            closeModal('modal-add-charge');
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al emitir el cargo.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

async function handleAddBankTransaction(e) {
    e.preventDefault();
    const date = document.getElementById('bank-date').value;
    const reference = document.getElementById('bank-reference').value;
    const amount = parseFloat(document.getElementById('bank-amount').value);
    const detail = document.getElementById('bank-detail').value;

    try {
        const res = await fetch('/api/bank-statement-add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date, reference, amount, detail })
        });
        
        if (res.ok) {
            showToast("Transacción bancaria registrada.");
            document.getElementById('form-add-bank').reset();
            const todayStr = new Date().toISOString().split('T')[0];
            document.getElementById('bank-date').value = todayStr;
            closeModal('modal-add-bank');
            loadAllData();
        } else {
            showToast("Error al registrar transacción bancaria.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

// Generic settings save helper to avoid configuration loss
async function saveSettings(updatedFields) {
    const payload = { ...settingsData, ...updatedFields };
    delete payload.google_credentials_json_configured;
    
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return res;
    } catch (err) {
        console.error("Error saving settings:", err);
        return null;
    }
}

async function handleSaveGoogleSettings(e) {
    e.preventDefault();
    const use_google_sheets = document.getElementById('config-use-sheets').checked;
    const spreadsheet_id = document.getElementById('config-spreadsheet-id').value;
    const google_credentials_json = document.getElementById('config-credentials-json').value;
    
    const res = await saveSettings({ use_google_sheets, spreadsheet_id, google_credentials_json });
    if (res && res.ok) {
        showToast("Configuración de Google Sheets guardada.");
        loadAllData();
    } else if (res) {
        const err = await res.json();
        showToast(err.detail || "Error al configurar Google Sheets.", "error");
    } else {
        showToast("Error de comunicación.", "error");
    }
}

async function handleSaveRulesSettings(e) {
    e.preventDefault();
    const late_fee_day = parseInt(document.getElementById('config-late-day').value);
    const late_fee_amount = parseFloat(document.getElementById('config-late-amount').value);
    const default_aliquot_base = parseFloat(document.getElementById('config-default-aliquot').value);
    const currency = document.getElementById('config-currency').value;
    const condo_name = document.getElementById('config-condo-name').value;
    const condo_ruc = document.getElementById('config-condo-ruc') ? document.getElementById('config-condo-ruc').value : "";
    const condo_address = document.getElementById('config-condo-address') ? document.getElementById('config-condo-address').value : "";
    const receipt_recipient_type = document.getElementById('config-receipt-recipient').value;
    const app_mode = document.getElementById('config-app-mode').value;
    
    const res = await saveSettings({ late_fee_day, late_fee_amount, default_aliquot_base, currency, condo_name, condo_ruc, condo_address, receipt_recipient_type, app_mode });
    if (res && res.ok) {
        showToast("Parámetros de reglas guardados.");
        loadAllData();
    } else {
        showToast("Error al guardar parámetros.", "error");
    }
}

// Test google sheets connection without saving checkbox
async function testGoogleConnection() {
    const spreadsheet_id = document.getElementById('config-spreadsheet-id').value;
    const google_credentials_json = document.getElementById('config-credentials-json').value;
    
    if (!spreadsheet_id || !google_credentials_json) {
        showToast("Por favor ingresa el Spreadsheet ID y las credenciales JSON.", "warning");
        return;
    }
    
    showToast("Probando conexión con Google Sheets...", "warning");
    
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                spreadsheet_id,
                google_credentials_json,
                use_google_sheets: true,
                late_fee_day: parseInt(document.getElementById('config-late-day').value),
                late_fee_amount: parseFloat(document.getElementById('config-late-amount').value),
                initial_bank_balance: settingsData.initial_bank_balance || 0.00,
                default_aliquot_base: settingsData.default_aliquot_base || 70.00
            })
        });
        
        if (res.ok) {
            showToast("¡Conexión Exitosa con Google Sheets!");
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Conexión fallida. Revisa el ID y las credenciales.", "error");
        }
    } catch (err) {
        showToast("Error de red al intentar conectar.", "error");
    }
}

// Modal Manual Validation Logic
function openManualValidationModal(id, type, unit, monthDesc, amount) {
    document.getElementById('modal-item-id').value = id;
    document.getElementById('modal-item-type').value = type;
    document.getElementById('modal-text-unit').innerText = `Depto ${unit}`;
    document.getElementById('modal-text-month').innerText = monthDesc;
    document.getElementById('modal-text-amount').innerText = `${settingsData.currency || '$'}${amount.toFixed(2)}`;
    document.getElementById('modal-input-amount').value = amount.toFixed(2);
    
    let existingItem = null;
    if (type === 'aliquot') {
        existingItem = aliquotsData.find(a => String(a.id) === String(id));
    } else {
        existingItem = additionalChargesData.find(c => String(c.id) === String(id));
    }
    
    // Set payment date from voucher/deposit if available, else today
    if (existingItem && existingItem.payment_date) {
        document.getElementById('modal-input-payment-date').value = existingItem.payment_date;
    } else {
        document.getElementById('modal-input-payment-date').value = new Date().toISOString().split('T')[0];
    }
    
    // Suggest reference from voucher if available, else default manual reference
    if (existingItem && existingItem.reference) {
        document.getElementById('modal-input-reference').value = existingItem.reference;
    } else {
        document.getElementById('modal-input-reference').value = `MAN-${intDate()}`;
    }
    
    // Suggest calculated late fee from voucher date if available
    if (existingItem && existingItem.late_fee !== undefined && existingItem.late_fee !== null) {
        document.getElementById('modal-input-late-fee').value = Number(existingItem.late_fee).toFixed(2);
    } else {
        document.getElementById('modal-input-late-fee').value = "0.00";
    }
    
    document.getElementById('modal-reconcile-manual').classList.add('open');
}

function closeManualValidationModal() {
    document.getElementById('modal-reconcile-manual').classList.remove('open');
}

async function handleSubmitManualValidation(e) {
    e.preventDefault();
    const id = document.getElementById('modal-item-id').value;
    const type = document.getElementById('modal-item-type').value;
    const reference = document.getElementById('modal-input-reference').value;
    const payment_date = document.getElementById('modal-input-payment-date').value;
    const late_fee = parseFloat(document.getElementById('modal-input-late-fee').value);
    const amount = parseFloat(document.getElementById('modal-input-amount').value);
    const receipt_recipient = document.getElementById('modal-input-recipient').value;

    try {
        const res = await fetch('/api/reconcile-manual', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, type, reference, payment_date, late_fee, amount, receipt_recipient })
        });
        
        if (res.ok) {
            showToast("Pago validado manualmente. Recibo PDF generado.");
            closeManualValidationModal();
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al validar el pago.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

// Helper intDate
function intDate() {
    return Math.floor(Date.now() / 1000);
}

// WHATSAPP CHAT SIMULATOR
function loadCaseTemplate(caseType) {
    const msgInput = document.getElementById('chat-message-text');
    let fileSimName = "";
    
    if (caseType === 'jenny') {
        msgInput.value = `Envío comprobante de pago del mes de Abril que hubo esté error.
Nombre: Jenny Portilla Bustamante 
Cédula: 171391685-4 
Departamento: 701
Mes: Abril/ 2026`;
        fileSimName = "comprobante_deposito_701.png";
    } 
    else if (caseType === 'sofia') {
        msgInput.value = `Hola, envío el pago de la alícuota del depto 302, correspondiente a Mayo de 2026.
Nombre: Sofia Herrera
Departamento: 302
Mes: Mayo / 2026`;
        fileSimName = "deposito_sofia_mayo.jpg";
    }
    else if (caseType === 'manual') {
        msgInput.value = `Envío el pago de Abril.
Nombre: Jenny Portilla
Departamento: 701
Mes: Abril`;
        fileSimName = "comprobante_falso_000000.pdf";
    }
    else if (caseType === 'extraordinaria') {
        msgInput.value = `Buenas tardes administrador, realizo la transferencia por concepto de la cuota extraordinaria para el mantenimiento del ascensor del depto 701.
Nombre: Jenny Portilla
Departamento: 701
Monto: 50.00`;
        fileSimName = "transferencia_ascensor_extra.jpg";
    }
    
    // Simulate file attachment
    if (fileSimName) {
        attachedFile = {
            name: fileSimName,
            isMock: true
        };
        const badge = document.getElementById('chat-file-badge');
        document.getElementById('file-name-text').innerText = fileSimName;
        badge.style.display = 'inline-flex';
    }
}

function handleFileSelected(e) {
    const file = e.target.files[0];
    if (file) {
        attachedFile = file;
        const badge = document.getElementById('chat-file-badge');
        document.getElementById('file-name-text').innerText = file.name;
        badge.style.display = 'inline-flex';
    }
}

function clearAttachedFile() {
    attachedFile = null;
    document.getElementById('chat-file-upload').value = "";
    document.getElementById('chat-file-badge').style.display = 'none';
}

async function handleSendWhatsapp(e) {
    e.preventDefault();
    const msgText = document.getElementById('chat-message-text').value;
    if (!msgText.trim()) return;

    // Append outgoing message bubble to chat
    appendChatMessage(msgText, 'outgoing', attachedFile ? attachedFile.name : null);
    
    // Reset inputs
    document.getElementById('chat-message-text').value = "";
    const currentAttachment = attachedFile;
    clearAttachedFile();
    
    // Show bot typing indicator
    const typingIndicator = appendTypingIndicator();
    
    // Send request to API
    try {
        const formData = new FormData();
        formData.append('message', msgText);
        
        if (currentAttachment) {
            if (currentAttachment.isMock) {
                // If it's a mock demo file, we send a blank file with the target name so backend OCR-simulator handles it
                const blob = new Blob(["demo-content"], { type: 'image/png' });
                formData.append('file', blob, currentAttachment.name);
            } else {
                formData.append('file', currentAttachment);
            }
        }
        
        const res = await fetch('/api/upload-receipt', {
            method: 'POST',
            body: formData
        });
        
        // Remove typing indicator
        typingIndicator.remove();
        
        if (res.ok) {
            const result = await res.json();
            
            if (result.status === 'success') {
                // Bot success response
                const botReply = `<b>[ASISTENTE] ✅ CONCILIACIÓN AUTOMÁTICA EXITOSA</b><br/><br/>
                ${result.message}<br/><br/>
                <b>Detalles Procesados:</b><br/>
                • Propietario: ${result.details.propietario}<br/>
                • Depto: ${result.details.unidad}<br/>
                • Tipo: ${result.details.tipo}<br/>
                ${result.details.mes ? `• Mes: ${result.details.mes}<br/>` : ''}
                • Monto: ${settingsData.currency || '$'}${result.details.monto.toFixed(2)}<br/>
                ${result.details.multa > 0 ? `• Multa: ${settingsData.currency || '$'}${result.details.multa.toFixed(2)}<br/>` : ''}
                • Transacción Ref: <code>${result.details.referencia}</code><br/>
                • Fecha Banco: ${result.details.fecha_pago}<br/><br/>
                <i>Tu recibo ha sido generado oficialmente. Puedes descargarlo aquí abajo.</i>`;
                
                appendChatMessage(botReply, 'incoming', null, result.receipt_url);
                showToast("Comprobante conciliado y validado en banco.");
            } 
            else if (result.status === 'manual_validation') {
                // Bot manual validation warning response
                const botReply = `<b>[ASISTENTE] ⚠️ ATENCIÓN: VALIDACIÓN MANUAL REQUERIDA</b><br/><br/>
                ${result.message}<br/><br/>
                El comprobante físico fue leído, pero la referencia bancaria <code>${result.details.referencia}</code> no coincide con un saldo disponible no conciliado en nuestro estado de cuenta bancario.<br/><br/>
                <b>Detalles Registrados:</b><br/>
                • Unidad: ${result.details.unidad} (${result.details.propietario})<br/>
                • Monto leído: ${settingsData.currency || '$'}${result.details.monto.toFixed(2)}<br/>
                • Periodo: ${result.details.mes || 'N/A'}<br/><br/>
                <i>El pago ha sido registrado en estado 'Validación Manual'. La administración verificará el caso a la brevedad.</i>`;
                
                appendChatMessage(botReply, 'incoming', null, null, 'warning-bubble');
                showToast("El depósito requiere validación manual del banco.", "warning");
            } 
            else {
                // Bot error
                appendChatMessage(`<b>[ASISTENTE] ❌ ERROR AL PROCESAR</b><br/><br/>${result.message}`, 'incoming', null, null, 'error-bubble');
                showToast(result.message, "error");
            }
            
            // Reload all lists to sync view
            loadAllData();
            
        } else {
            typingIndicator.remove();
            appendChatMessage("<b>[ASISTENTE] ❌ ERROR</b><br/>No se pudo comunicar con el servidor de análisis de comprobantes.", 'incoming');
            showToast("Error al procesar el mensaje.", "error");
        }
    } catch (err) {
        typingIndicator.remove();
        appendChatMessage("<b>[ASISTENTE] ❌ ERROR DE RED</b><br/>Ocurrió un fallo de red durante el procesamiento.", 'incoming');
        showToast("Error de conexión.", "error");
    }
}

function appendChatMessage(text, direction, fileName = null, receiptUrl = null, bubbleClass = '') {
    const container = document.getElementById('chat-messages-container');
    const msgDiv = document.createElement('div');
    msgDiv.className = `message message-${direction} ${bubbleClass}`;
    
    let filePreviewHtml = '';
    if (fileName) {
        filePreviewHtml = `
            <div class="attached-file-preview">
                <i data-lucide="image" style="width:14px;height:14px;"></i>
                <span>${fileName}</span>
            </div>
        `;
    }
    
    let receiptBtnHtml = '';
    if (receiptUrl) {
        receiptBtnHtml = `
            <a href="${receiptUrl}" target="_blank" class="btn-chat-receipt">
                <i data-lucide="file-text" style="width:14px;height:14px;"></i>
                Descargar Recibo Oficial (PDF)
            </a>
        `;
    }

    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    
    msgDiv.innerHTML = `
        ${filePreviewHtml}
        <p>${text.replace(/\n/g, '<br/>')}</p>
        ${receiptBtnHtml}
        <span class="message-time">${timeStr}</span>
    `;
    
    container.appendChild(msgDiv);
    lucide.createIcons();
    
    // Scroll to bottom
    container.scrollTop = container.scrollHeight;
    return msgDiv;
}

function appendTypingIndicator() {
    const container = document.getElementById('chat-messages-container');
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message message-incoming typing-message-container';
    msgDiv.innerHTML = `
        <div class="typing-indicator">
            <span></span>
            <span></span>
            <span></span>
        </div>
        <span class="typing-text">Asistente está conciliando comprobante con banco...</span>
    `;
    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;
    return msgDiv;
}

/* BANK STATEMENT MULTI-FORMAT UPLOAD & MANUAL RECONCILIATION CODES */

// Drag and Drop Zone Initialization
function initStatementUpload() {
    const dropZone = document.getElementById('statement-upload-zone');
    const fileInput = document.getElementById('statement-file-input');

    if (!dropZone || !fileInput) return;

    // Trigger file selection on click
    dropZone.addEventListener('click', () => fileInput.click());

    // Highlight drop zone on dragover
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    // Remove highlight on dragleave
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    // Handle dropped file
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleStatementFile(e.dataTransfer.files[0]);
        }
    });

    // Handle selected file from dialog
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleStatementFile(e.target.files[0]);
        }
    });
}

function handleStatementFile(file) {
    statementUploadedFile = file;
    document.getElementById('statement-file-name-text').innerText = file.name;
    document.getElementById('statement-file-info').style.display = 'flex';
    document.getElementById('statement-upload-zone').style.display = 'none';
}

function clearStatementFile() {
    statementUploadedFile = null;
    document.getElementById('statement-file-input').value = '';
    document.getElementById('statement-file-info').style.display = 'none';
    document.getElementById('statement-upload-zone').style.display = 'flex';
}

async function uploadStatementFile() {
    if (!statementUploadedFile) {
        showToast("Selecciona o arrastra un archivo primero.", "warning");
        return;
    }

    const processBtn = document.getElementById('btn-process-statement');
    processBtn.disabled = true;
    processBtn.innerText = "Procesando...";

    const formData = new FormData();
    formData.append('file', statementUploadedFile);

    try {
        const res = await fetch('/api/upload-bank-statement', {
            method: 'POST',
            body: formData
        });

        if (res.ok) {
            const result = await res.json();
            showToast(`Estado de cuenta cargado. ${result.added} transacciones nuevas, ${result.duplicates} duplicados omitidos.`);
            clearStatementFile();
            closeModal('modal-upload-statement');
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al subir el estado de cuenta.", "error");
            processBtn.disabled = false;
            processBtn.innerText = "Procesar";
        }
    } catch (err) {
        showToast("Error de conexión al servidor.", "error");
        processBtn.disabled = false;
        processBtn.innerText = "Procesar";
    }
}

// Collapsible Form
function toggleManualBankForm() {
    const form = document.getElementById('form-add-bank');
    const icon = document.getElementById('icon-toggle-manual-bank');
    
    if (form.style.display === 'none') {
        form.style.display = 'flex';
        icon.style.transform = 'rotate(180deg)';
    } else {
        form.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
    }
}

// Interactive reconciliation lists rendering
function renderReconciliationLists() {
    const paymentsList = document.getElementById('reconcile-payments-list');
    const bankList = document.getElementById('reconcile-bank-list');
    
    if (!paymentsList || !bankList) return;

    paymentsList.innerHTML = "";
    bankList.innerHTML = "";

    const currency = settingsData.currency || '$';

    // 1. Gather all pending payments
    const pendingPayments = [];
    
    aliquotsData.forEach(a => {
        if (a.status === 'Pendiente' || a.status === 'Validación Manual') {
            pendingPayments.push({
                id: a.id,
                type: 'aliquot',
                unit: a.unit,
                owner: a.owner,
                label: `Depto ${a.unit} - Alícuota ${a.month}`,
                amount: a.amount + a.late_fee,
                status: a.status,
                meta: `Mes: ${a.month} / ${a.year}`
            });
        }
    });

    additionalChargesData.forEach(c => {
        if (c.status === 'Pendiente' || c.status === 'Validación Manual') {
            pendingPayments.push({
                id: c.id,
                type: 'charge',
                unit: c.unit,
                owner: c.owner,
                label: `Depto ${c.unit} - ${c.type}`,
                amount: c.amount,
                status: c.status,
                meta: c.description
            });
        }
    });

    document.getElementById('reconcile-payments-count').innerText = pendingPayments.length;

    if (pendingPayments.length === 0) {
        paymentsList.innerHTML = getEmptyStateHtml('inbox', 'No hay cobros pendientes.');
        lucide.createIcons();
    } else {
        pendingPayments.forEach(p => {
            const card = document.createElement('div');
            const isSelected = selectedPayment && selectedPayment.id === p.id && selectedPayment.type === p.type;
            card.className = `reconcile-card ${isSelected ? 'selected-payment' : ''}`;
            
            card.innerHTML = `
                <div class="reconcile-card-header">
                    <span class="reconcile-card-title">${p.label}</span>
                    <span class="reconcile-card-amount">${currency}${p.amount.toFixed(2)}</span>
                </div>
                <div class="reconcile-card-meta">${p.owner}</div>
                <div class="reconcile-card-desc">${p.meta}</div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px;">
                    <span class="status-badge ${p.status === 'Validación Manual' ? 'manual' : 'pending'}" style="font-size:0.65rem; padding: 2px 6px;">
                        ${p.status === 'Validación Manual' ? 'Val. Manual' : 'Pendiente'}
                    </span>
                </div>
            `;
            
            card.addEventListener('click', () => selectPaymentItem(p));
            paymentsList.appendChild(card);
        });
    }

    // 2. Gather unreconciled bank transactions
    const pendingBankTx = bankStatementData.filter(b => !b.reconciled);
    document.getElementById('reconcile-bank-count').innerText = pendingBankTx.length;

    if (pendingBankTx.length === 0) {
        bankList.innerHTML = getEmptyStateHtml('landmark', 'No hay depósitos sin conciliar.');
        lucide.createIcons();
    } else {
        pendingBankTx.forEach(b => {
            const card = document.createElement('div');
            const isSelected = selectedBankTx && selectedBankTx.reference === b.reference;
            card.className = `reconcile-card ${isSelected ? 'selected-bank' : ''}`;
            
            card.innerHTML = `
                <div class="reconcile-card-header">
                    <span class="reconcile-card-title">Ref: ${b.reference}</span>
                    <span class="reconcile-card-amount">${currency}${b.amount.toFixed(2)}</span>
                </div>
                <div class="reconcile-card-meta">${b.date}</div>
                <div class="reconcile-card-desc">${b.detail}</div>
            `;
            
            card.addEventListener('click', () => selectBankItem(b));
            bankList.appendChild(card);
        });
    }
}

function selectPaymentItem(p) {
    if (selectedPayment && selectedPayment.id === p.id && selectedPayment.type === p.type) {
        // Toggle off
        selectedPayment = null;
    } else {
        selectedPayment = p;
    }
    updateReconciliationDisplay();
    renderReconciliationLists();
}

function selectBankItem(b) {
    if (selectedBankTx && selectedBankTx.reference === b.reference) {
        // Toggle off
        selectedBankTx = null;
    } else {
        selectedBankTx = b;
    }
    updateReconciliationDisplay();
    renderReconciliationLists();
}

function updateReconciliationDisplay() {
    const paymentDisplay = document.getElementById('selected-payment-display');
    const bankDisplay = document.getElementById('selected-bank-display');
    const reconcileBtn = document.getElementById('btn-reconcile-pair');
    const warningText = document.getElementById('match-amount-diff-warning');
    const currency = settingsData.currency || '$';

    // Payment Box
    if (selectedPayment) {
        paymentDisplay.innerText = `${selectedPayment.label} (${currency}${selectedPayment.amount.toFixed(2)})`;
        paymentDisplay.className = "selected-match-display filled-payment";
    } else {
        paymentDisplay.innerText = "Ninguno seleccionado";
        paymentDisplay.className = "selected-match-display empty";
    }

    // Bank Box
    if (selectedBankTx) {
        bankDisplay.innerText = `Ref: ${selectedBankTx.reference} (${currency}${selectedBankTx.amount.toFixed(2)})`;
        bankDisplay.className = "selected-match-display filled-bank";
    } else {
        bankDisplay.innerText = "Ninguna seleccionada";
        bankDisplay.className = "selected-match-display empty";
    }

    // Warn if amounts differ
    if (selectedPayment && selectedBankTx) {
        const diff = Math.abs(selectedPayment.amount - selectedBankTx.amount);
        if (diff > 0.01) {
            warningText.style.display = 'flex';
        } else {
            warningText.style.display = 'none';
        }
        reconcileBtn.disabled = false;
    } else {
        warningText.style.display = 'none';
        reconcileBtn.disabled = true;
    }
}

function clearReconciliationSelection() {
    selectedPayment = null;
    selectedBankTx = null;
    updateReconciliationDisplay();
}

async function reconcileSelectedPair() {
    if (!selectedPayment || !selectedBankTx) return;

    const reconcileBtn = document.getElementById('btn-reconcile-pair');
    reconcileBtn.disabled = true;
    reconcileBtn.innerHTML = "Procesando...";

    try {
        const res = await fetch('/api/reconcile-pair', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                payment_id: selectedPayment.id,
                payment_type: selectedPayment.type,
                bank_reference: selectedBankTx.reference,
                receipt_recipient: document.getElementById('pair-input-recipient').value
            })
        });

        if (res.ok) {
            const result = await res.json();
            showToast("Vinculación y conciliación exitosa. Recibo PDF generado.");
            clearReconciliationSelection();
            
            // If aliquot, check if PDF receipt URL is available and offer download
            if (result.receipt_url) {
                // Open receipt in new tab
                window.open(result.receipt_url, '_blank');
            }
            
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al realizar la conciliación.", "error");
            reconcileBtn.disabled = false;
            reconcileBtn.innerHTML = '<i data-lucide="link"></i> Vincular y Conciliar Seleccionados';
        }
    } catch (err) {
        showToast("Error de comunicación con el servidor.", "error");
        reconcileBtn.disabled = false;
        reconcileBtn.innerHTML = '<i data-lucide="link"></i> Vincular y Conciliar Seleccionados';
    }
    lucide.createIcons();
}

// Reports Exports Redirects
function exportAliquotsCsv() {
    window.location.href = '/api/aliquots/export/csv';
}

function exportExpensesCsv() {
    window.location.href = '/api/expenses/export/csv';
}

function exportDebtorsCsv() {
    window.location.href = '/api/debtors/export/csv';
}

function exportFinancialPdf() {
    window.location.href = '/api/reports/financial-pdf';
}

async function handleUploadReceiptManual(e) {
    e.preventDefault();
    const fileInput = document.getElementById('receipt-manual-file');
    const textInput = document.getElementById('receipt-manual-text');
    const unitSelect = document.getElementById('receipt-manual-unit');
    const btn = e.target.querySelector('button[type="submit"]');

    const file = fileInput.files[0];
    const message = textInput.value;
    const unit = unitSelect ? unitSelect.value : "";

    btn.disabled = true;
    btn.innerText = "Procesando...";

    const formData = new FormData();
    formData.append('message', message);
    if (file) {
        formData.append('file', file);
    }
    if (unit) {
        formData.append('unit', unit);
    }

    try {
        const res = await fetch('/api/upload-receipt', {
            method: 'POST',
            body: formData
        });

        if (res.ok) {
            const result = await res.json();
            if (result.status === 'success') {
                showToast(`Comprobante procesado: Conciliación automática exitosa!`);
            } else if (result.status === 'manual_validation') {
                showToast(`Comprobante registrado: Referencia pendiente de confirmación bancaria (Validación Manual).`, 'warning');
            } else {
                showToast(result.message || "Error al procesar el comprobante.", "error");
            }
            // Reset form
            document.getElementById('form-upload-receipt-manual').reset();
            closeModal('modal-upload-receipt-manual');
            loadAllData();
        } else {
            showToast("Error de comunicación con el servidor.", "error");
        }
    } catch (err) {
        showToast("Error de red.", "error");
    } finally {
        btn.disabled = false;
        btn.innerText = "Procesar Comprobante";
    }
}

async function runMassReconciliation() {
    showToast("Iniciando conciliación masiva de comprobantes...", "warning");
    
    try {
        const res = await fetch('/api/reconcile-mass', {
            method: 'POST'
        });

        if (res.ok) {
            const result = await res.json();
            showToast(`Conciliación masiva finalizada. Se conciliaron ${result.total_reconciled} registros automáticamente (Alícuotas: ${result.aliquots_reconciled}, Cargos: ${result.charges_reconciled}).`);
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al realizar la conciliación masiva.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación con el servidor.", "error");
    }
}

// Units CRUD and Initial Balances Controller Logic
async function fetchUnits() {
    const res = await fetch('/api/units');
    unitsData = await res.json();
    sortUnitsData();
    renderUnitsTable();
    updateSortHeadersUI();
}

function sortUnitsData() {
    unitsData.sort((a, b) => {
        let valA = a[unitsSortKey] || '';
        let valB = b[unitsSortKey] || '';
        
        if (unitsSortKey === 'id') {
            const numA = parseInt(valA, 10);
            const numB = parseInt(valB, 10);
            if (!isNaN(numA) && !isNaN(numB)) {
                return unitsSortAsc ? numA - numB : numB - numA;
            }
        }
        
        valA = String(valA).toLowerCase();
        valB = String(valB).toLowerCase();
        
        if (valA < valB) return unitsSortAsc ? -1 : 1;
        if (valA > valB) return unitsSortAsc ? 1 : -1;
        return 0;
    });
}

function toggleSortUnits(key) {
    if (unitsSortKey === key) {
        unitsSortAsc = !unitsSortAsc;
    } else {
        unitsSortKey = key;
        unitsSortAsc = true;
    }
    sortUnitsData();
    renderUnitsTable();
    updateSortHeadersUI();
}

function updateSortHeadersUI() {
    const table = document.getElementById('table-units');
    if (!table) return;
    const headers = table.querySelectorAll('th.sortable');
    headers.forEach(h => {
        const icon = h.querySelector('i');
        if (icon) {
            icon.setAttribute('data-lucide', 'chevrons-up-down');
        }
    });
    
    // Match header elements by their onClick handler target
    headers.forEach(h => {
        const clickAttr = h.getAttribute('onclick') || '';
        if (clickAttr.includes(`'${unitsSortKey}'`)) {
            const icon = h.querySelector('i');
            if (icon) {
                icon.setAttribute('data-lucide', unitsSortAsc ? 'chevron-up' : 'chevron-down');
            }
        }
    });
    lucide.createIcons();
}

function renderUnitsTable() {
    const tbody = document.getElementById('table-units').querySelector('tbody');
    tbody.innerHTML = "";
    const currency = settingsData.currency || '$';

    if (unitsData.length === 0) {
        tbody.innerHTML = getTableEmptyStateHtml(5, 'home', 'No hay departamentos registrados.', 'Registra una unidad usando el formulario o carga un archivo masivo.');
        lucide.createIcons();
        return;
    }

    unitsData.forEach(u => {
        const row = document.createElement('tr');
        
        const ownerContact = [
            u.phone1 ? `Tlf: ${u.phone1}` : '',
            u.email1 ? `Mail: ${u.email1}` : ''
        ].filter(x => x).join('<br/>');

        const tenantContact = [
            u.phone2 ? `Tlf: ${u.phone2}` : '',
            u.email2 ? `Mail: ${u.email2}` : ''
        ].filter(x => x).join('<br/>');

        row.innerHTML = `
            <td><strong>${u.id}</strong></td>
            <td>
                <div><b>${u.owner}</b></div>
                <div style="font-size:0.75rem; color:var(--text-muted);">${ownerContact || '-'}</div>
            </td>
            <td>
                <div><b>${u.tenant || '-'}</b></div>
                <div style="font-size:0.75rem; color:var(--text-muted);">${tenantContact || ''}</div>
            </td>
            <td>
                <div style="display:flex; gap:6px; flex-wrap:wrap;">
                    <button class="btn btn-secondary-sm" onclick="emitAdminCertificate('${u.id}')" title="Emitir Certificado de No Adeudar" style="display:flex; align-items:center; gap:4px; padding:6px 10px; border-color: rgba(99, 102, 241, 0.4); color: #c7d2fe;">
                        <i data-lucide="award" style="width:12px;height:12px;"></i> Certificado
                    </button>
                    <button class="btn btn-secondary-sm" onclick="editUnit('${u.id}')" style="display:flex; align-items:center; gap:4px; padding:6px 10px;"><i data-lucide="edit-3" style="width:12px;height:12px;"></i> Editar</button>
                    <button class="btn btn-secondary-sm" onclick="deleteUnit('${u.id}')" style="display:flex; align-items:center; gap:4px; padding:6px 10px; background:#ef4444; border-color:#ef4444; color:white;"><i data-lucide="trash-2" style="width:12px;height:12px;"></i> Eliminar</button>
                </div>
            </td>
        `;
        tbody.appendChild(row);
    });
    lucide.createIcons();
}

function editUnit(id) {
    const u = unitsData.find(x => x.id === id);
    if (!u) return;

    editingUnitId = id;
    document.getElementById('unit-id').value = u.id;
    document.getElementById('unit-id').readOnly = true;
    document.getElementById('unit-owner').value = u.owner;
    document.getElementById('unit-cedula-owner').value = u.cedula_owner || '';
    document.getElementById('unit-phone1').value = u.phone1 || '';
    document.getElementById('unit-email1').value = u.email1 || '';
    document.getElementById('unit-tenant').value = u.tenant || '';
    document.getElementById('unit-cedula-tenant').value = u.cedula_tenant || '';
    document.getElementById('unit-phone2').value = u.phone2 || '';
    document.getElementById('unit-email2').value = u.email2 || '';
    document.getElementById('unit-receipt-recipient').value = u.receipt_recipient_type || 'general';

    document.getElementById('department-form-title').innerHTML = `<i data-lucide="edit"></i> Editar Departamento ${u.id}`;
    document.getElementById('btn-cancel-edit-unit').style.display = 'inline-block';
    document.getElementById('btn-submit-unit').innerText = 'Actualizar Departamento';
    lucide.createIcons();
    
    openModal('modal-add-unit');
}

function cancelUnitEdit() {
    editingUnitId = null;
    document.getElementById('form-add-unit').reset();
    document.getElementById('unit-id').readOnly = false;
    document.getElementById('department-form-title').innerHTML = `<i data-lucide="home"></i> Registrar Departamento`;
    document.getElementById('btn-cancel-edit-unit').style.display = 'none';
    document.getElementById('btn-submit-unit').innerText = 'Guardar Departamento';
    lucide.createIcons();
    closeModal('modal-add-unit');
}

async function deleteUnit(id) {
    if (!confirm(`¿Estás seguro de que deseas eliminar el departamento ${id}? Esta acción es irreversible y eliminará todos sus usuarios, alícuotas y cargos asociados.`)) {
        return;
    }
    
    try {
        const res = await fetch(`/api/units/${id}`, {
            method: 'DELETE'
        });
        
        if (res.ok) {
            showToast(`Departamento ${id} eliminado exitosamente.`);
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al eliminar el departamento.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación con el servidor.", "error");
    }
}

async function handleAddUnit(e) {
    e.preventDefault();
    const id = document.getElementById('unit-id').value;
    const owner = document.getElementById('unit-owner').value;
    const cedula_owner = document.getElementById('unit-cedula-owner').value;
    const phone1 = document.getElementById('unit-phone1').value;
    const email1 = document.getElementById('unit-email1').value;
    const tenant = document.getElementById('unit-tenant').value;
    const cedula_tenant = document.getElementById('unit-cedula-tenant').value;
    const phone2 = document.getElementById('unit-phone2').value;
    const email2 = document.getElementById('unit-email2').value;
    const receipt_recipient_type = document.getElementById('unit-receipt-recipient').value;

    try {
        const res = await fetch('/api/units', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, owner, phone1, email1, tenant, phone2, email2, cedula_owner, cedula_tenant, receipt_recipient_type })
        });

        if (res.ok) {
            showToast(editingUnitId ? "Departamento actualizado exitosamente." : "Departamento registrado exitosamente.");
            cancelUnitEdit();
            loadAllData();
        } else {
            showToast("Error al guardar el departamento.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

async function handleSaveInitialBalance(e) {
    e.preventDefault();
    const initial_bank_balance = parseFloat(document.getElementById('config-initial-bank-balance').value) || 0.0;
    
    try {
        const res = await fetch('/api/initial-balances', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                initial_bank_balance,
                initial_reserve_fund: 0.0,
                cut_off_date: '',
                description: 'Saldo inicial de apertura'
            })
        });
        if (res.ok) {
            showToast("Saldo inicial de banco guardado exitosamente en base de datos.");
            loadAllData();
        } else {
            showToast("Error al guardar saldo inicial.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

async function handleAddInitialDebt(e) {
    e.preventDefault();
    const unit = document.getElementById('initial-debt-unit').value;
    const amount = parseFloat(document.getElementById('initial-debt-amount').value);
    const description = document.getElementById('initial-debt-description').value;

    if (!unit) {
        showToast("Por favor selecciona una unidad.", "warning");
        return;
    }

    try {
        const res = await fetch('/api/additional-charges', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                unit,
                type: 'Deuda Inicial',
                amount,
                description,
                issue_date: '2026-01-01'
            })
        });

        if (res.ok) {
            showToast("Deuda inicial registrada correctamente.");
            document.getElementById('form-add-initial-debt').reset();
            document.getElementById('initial-debt-description').value = "Saldo Deudor Histórico Inicial";
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al registrar la deuda inicial.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

async function uploadUnitsFile(event) {
    const file = event.target.files[0];
    if (!file) return;

    showToast("Subiendo y procesando departamentos...", "warning");

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch('/api/upload-units', {
            method: 'POST',
            body: formData
        });

        if (res.ok) {
            const result = await res.json();
            showToast(`Carga masiva completada: ${result.message}`);
            event.target.value = '';
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al subir los departamentos.", "error");
        }
    } catch (err) {
        showToast("Error de conexión al servidor.", "error");
    }
}

async function handleSaveNotificationSettings(e) {
    e.preventDefault();
    const smtp_host = document.getElementById('config-smtp-host').value;
    const smtp_port = parseInt(document.getElementById('config-smtp-port').value);
    const smtp_user = document.getElementById('config-smtp-user').value;
    const smtp_password = document.getElementById('config-smtp-password').value;
    const smtp_from = document.getElementById('config-smtp-from').value;
    const twilio_sid = document.getElementById('config-twilio-sid').value;
    const twilio_token = document.getElementById('config-twilio-token').value;
    const twilio_whatsapp_from = document.getElementById('config-twilio-from').value;
    const whatsapp_provider = document.getElementById('config-whatsapp-provider').value;
    const meta_wa_token = document.getElementById('config-meta-token').value;
    const meta_wa_phone_number_id = document.getElementById('config-meta-phone-id').value;
    const meta_wa_business_account_id = document.getElementById('config-meta-waba-id').value;
    const meta_wa_verify_token = document.getElementById('config-meta-verify-token').value;

    const res = await saveSettings({
        smtp_host,
        smtp_port,
        smtp_user,
        smtp_password,
        smtp_from,
        twilio_sid,
        twilio_token,
        twilio_whatsapp_from,
        whatsapp_provider,
        meta_wa_token,
        meta_wa_phone_number_id,
        meta_wa_business_account_id,
        meta_wa_verify_token
    });

    if (res && res.ok) {
        showToast("Configuración de notificaciones guardada.");
        loadAllData();
    } else if (res) {
        const err = await res.json();
        showToast(err.detail || "Error al guardar notificaciones.", "error");
    } else {
        showToast("Error de comunicación.", "error");
    }
}

async function handleTestSmtpConnection() {
    const btn = document.getElementById('btn-test-smtp');
    const statusEl = document.getElementById('smtp-test-status');
    
    const smtp_host = document.getElementById('config-smtp-host').value.trim();
    const smtp_port = parseInt(document.getElementById('config-smtp-port').value) || 465;
    const smtp_user = document.getElementById('config-smtp-user').value.trim();
    const smtp_password = document.getElementById('config-smtp-password').value.trim();
    const smtp_from = document.getElementById('config-smtp-from').value.trim();
    
    if (!smtp_host || !smtp_user) {
        showToast("Por favor ingresa el servidor y usuario SMTP antes de probar.", "error");
        return;
    }
    
    btn.disabled = true;
    btn.innerHTML = `<i data-lucide="loader-2" class="spin"></i> Probando conexión...`;
    if (window.lucide) lucide.createIcons();
    if (statusEl) {
        statusEl.style.display = 'inline';
        statusEl.style.color = '#3b82f6';
        statusEl.innerText = "Conectando al servidor SMTP y enviando correo de prueba...";
    }
    
    try {
        const res = await fetch('/api/settings/test-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                smtp_host,
                smtp_port,
                smtp_user,
                smtp_password,
                smtp_from
            })
        });
        
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Correo de prueba enviado con éxito.", "success");
            if (statusEl) {
                statusEl.style.color = '#16a34a';
                statusEl.innerText = "✅ Conexión exitosa. Correo recibido en la bandeja.";
            }
        } else {
            showToast(data.detail || "Error en la prueba SMTP.", "error");
            if (statusEl) {
                statusEl.style.color = '#dc2626';
                statusEl.innerText = `❌ Error: ${data.detail}`;
            }
        }
    } catch (err) {
        showToast("Error de comunicación con el servidor.", "error");
        if (statusEl) {
            statusEl.style.color = '#dc2626';
            statusEl.innerText = "❌ Error de conexión al servidor.";
        }
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<i data-lucide="send"></i> Probar Conexión SMTP`;
        if (window.lucide) lucide.createIcons();
    }
}

async function loadNotificationLogs() {
    const tableBody = document.getElementById('table-notification-logs').querySelector('tbody');
    if (!tableBody) return;
    
    try {
        const res = await fetch('/api/notification-logs');
        if (!res.ok) return;
        const logs = await res.json();
        
        tableBody.innerHTML = "";
        if (logs.length === 0) {
            tableBody.innerHTML = getTableEmptyStateHtml(8, 'history', 'No se han registrado envíos de notificaciones.', 'Las notificaciones enviadas aparecerán aquí.');
            lucide.createIcons();
            return;
        }
        
        // Show newest first
        const sortedLogs = [...logs].reverse();
        
        sortedLogs.forEach(l => {
            const row = document.createElement('tr');
            
            let statusClass = "pending";
            if (l.status === 'Enviado') statusClass = "paid";
            else if (l.status === 'Error') statusClass = "pending";
            
            const badgeClass = l.channel === 'Email' ? 'text-purple' : 'text-blue';
            const modeBadge = l.mode === 'Real' ? '<span class="status-badge paid" style="background:var(--blue); color:white;">Real</span>' : '<span class="status-badge pending" style="background:#64748b; color:white;">Simulado</span>';
            
            row.innerHTML = `
                <td><small>${l.timestamp}</small></td>
                <td><strong>${l.unit}</strong></td>
                <td>${l.recipient}</td>
                <td><span class="${badgeClass}"><b>${l.channel}</b></span></td>
                <td><small>${l.destination}</small></td>
                <td>${modeBadge}</td>
                <td><span class="status-badge ${statusClass}">${l.status}</span></td>
                <td><small>${l.details || '-'}</small></td>
            `;
            tableBody.appendChild(row);
        });
    } catch (err) {
        console.error("Error loading notification logs:", err);
    }
}

async function resendReceipt(id, type) {
    showToast("Reenviando recibo por Email y WhatsApp...", "warning");
    
    try {
        const res = await fetch('/api/receipts/resend', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, type })
        });
        
        if (res.ok) {
            const data = await res.json();
            showToast(data.message || "Recibo reenviado correctamente.");
            loadNotificationLogs();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al reenviar el recibo.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación con el servidor.", "error");
    }
}

// UI/UX empty state helpers
function getEmptyStateHtml(iconName, message, subtitle = "") {
    return `
        <div class="empty-state-wrapper">
            <i data-lucide="${iconName}"></i>
            <p>${message}</p>
            ${subtitle ? `<span>${subtitle}</span>` : ""}
        </div>
    `;
}

function getTableEmptyStateHtml(cols, iconName, message, subtitle = "") {
    return `
        <tr>
            <td colspan="${cols}" style="padding:0; border:none;">
                <div class="empty-state-wrapper" style="margin: 20px;">
                    <i data-lucide="${iconName}"></i>
                    <p>${message}</p>
                    ${subtitle ? `<span>${subtitle}</span>` : ""}
                </div>
            </td>
        </tr>
    `;
}

// Render pagination controls dynamically
function renderPagination(containerId, currentPage, totalItems, limit, onPageChange) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const totalPages = Math.ceil(totalItems / limit) || 1;
    
    // Calculate range info
    const startRange = totalItems === 0 ? 0 : (currentPage - 1) * limit + 1;
    const endRange = Math.min(currentPage * limit, totalItems);

    // Build the info text
    let html = `
        <div class="pagination-info">
            Mostrando <strong>${startRange}-${endRange}</strong> de <strong>${totalItems}</strong> registros
        </div>
    `;

    // Only render page buttons if we have more than 1 page
    if (totalPages > 1) {
        html += `<div class="pagination-buttons">`;
        
        // Previous button
        const prevDisabled = currentPage === 1 ? 'disabled' : '';
        html += `<button class="pagination-btn" ${prevDisabled} data-page="${currentPage - 1}">
            <i data-lucide="chevron-left" style="width:14px;height:14px;"></i>
        </button>`;

        // Numbered page buttons
        const range = 2; // How many buttons around current page
        for (let i = 1; i <= totalPages; i++) {
            if (i === 1 || i === totalPages || (i >= currentPage - range && i <= currentPage + range)) {
                const activeClass = i === currentPage ? 'active' : '';
                html += `<button class="pagination-btn ${activeClass}" data-page="${i}">${i}</button>`;
            } else if (i === currentPage - range - 1 || i === currentPage + range + 1) {
                html += `<span style="color:var(--text-muted); padding:0 4px;">...</span>`;
            }
        }

        // Next button
        const nextDisabled = currentPage === totalPages ? 'disabled' : '';
        html += `<button class="pagination-btn" ${nextDisabled} data-page="${currentPage + 1}">
            <i data-lucide="chevron-right" style="width:14px;height:14px;"></i>
        </button>`;

        html += `</div>`;
    }

    container.innerHTML = html;

    // Attach event listeners to buttons
    container.querySelectorAll('.pagination-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const page = parseInt(btn.getAttribute('data-page'));
            if (page && page >= 1 && page <= totalPages && page !== currentPage) {
                onPageChange(page);
            }
        });
    });

    lucide.createIcons();
}

function toggleWhatsappSettingsVisibility() {
    const provider = document.getElementById('config-whatsapp-provider').value;
    const twilioGroup = document.getElementById('settings-group-twilio');
    const metaGroup = document.getElementById('settings-group-meta');
    
    if (provider === 'twilio') {
        twilioGroup.style.display = 'block';
        metaGroup.style.display = 'none';
    } else if (provider === 'meta') {
        twilioGroup.style.display = 'none';
        metaGroup.style.display = 'block';
    } else {
        twilioGroup.style.display = 'none';
        metaGroup.style.display = 'none';
    }
}

// Authentication & Roles UI Functions
function showLoginAlert(message, type = 'error', html = false) {
    const alertBox = document.getElementById('login-alert-box');
    if (!alertBox) return;
    alertBox.className = `login-alert-box ${type}`;
    let icon = 'alert-circle';
    if (type === 'success') icon = 'check-circle-2';
    if (type === 'warning') icon = 'alert-triangle';
    
    if (html) {
        alertBox.innerHTML = `<i data-lucide="${icon}" style="flex-shrink:0; margin-top:2px;"></i><div>${message}</div>`;
    } else {
        alertBox.innerHTML = `<i data-lucide="${icon}" style="flex-shrink:0; margin-top:2px;"></i><div>${escapeHtml(message)}</div>`;
    }
    alertBox.style.display = 'flex';
    if (window.lucide) lucide.createIcons({ root: alertBox });
}

function clearLoginAlert() {
    const alertBox = document.getElementById('login-alert-box');
    if (alertBox) alertBox.style.display = 'none';
}

function setLoginMode(mode) {
    clearLoginAlert();
    const loginForm = document.getElementById('form-login');
    const recoverForm = document.getElementById('form-recover');
    const resetForm = document.getElementById('form-reset-password');
    const tabLogin = document.getElementById('btn-tab-login');
    const tabRecover = document.getElementById('btn-tab-recover');
    const subtitle = document.getElementById('login-subtitle');
    const tabsContainer = document.getElementById('login-form-tabs');

    if (mode === 'login') {
        if (loginForm) loginForm.style.display = 'block';
        if (recoverForm) recoverForm.style.display = 'none';
        if (resetForm) resetForm.style.display = 'none';
        if (tabLogin) tabLogin.classList.add('active');
        if (tabRecover) tabRecover.classList.remove('active');
        if (tabsContainer) tabsContainer.style.display = 'flex';
        if (subtitle) subtitle.innerText = "Ingresa tus credenciales para acceder al sistema";
    } else if (mode === 'recover') {
        if (loginForm) loginForm.style.display = 'none';
        if (recoverForm) recoverForm.style.display = 'block';
        if (resetForm) resetForm.style.display = 'none';
        if (tabLogin) tabLogin.classList.remove('active');
        if (tabRecover) tabRecover.classList.add('active');
        if (tabsContainer) tabsContainer.style.display = 'flex';
        if (subtitle) subtitle.innerText = "Solicita una clave temporal de acceso";

        // Sync role, cedula, and email from login form if present
        const currentRole = document.getElementById('login-role') ? document.getElementById('login-role').value : 'superadmin';
        const currentCedula = document.getElementById('login-cedula') ? document.getElementById('login-cedula').value : '';
        const currentEmail = document.getElementById('login-email') ? document.getElementById('login-email').value : '';
        
        const recoverRole = document.getElementById('recover-role');
        const recoverCedula = document.getElementById('recover-cedula');
        const recoverEmail = document.getElementById('recover-email');
        
        if (recoverRole) recoverRole.value = currentRole;
        if (recoverCedula && currentCedula) recoverCedula.value = currentCedula;
        if (recoverEmail && currentEmail) recoverEmail.value = currentEmail;
        
        if (currentRole === 'superadmin' || currentRole === 'admin') {
            onRecoverRoleChange();
        }
    } else if (mode === 'reset') {
        if (loginForm) loginForm.style.display = 'none';
        if (recoverForm) recoverForm.style.display = 'none';
        if (resetForm) resetForm.style.display = 'block';
        if (tabsContainer) tabsContainer.style.display = 'none';
        if (subtitle) subtitle.innerText = "Establece tu nueva contraseña permanente";
    }
}

function onLoginRoleChange() {
    clearLoginAlert();
    const role = document.getElementById('login-role').value;
    const cedulaInput = document.getElementById('login-cedula');
    const emailInput = document.getElementById('login-email');
    const passwordInput = document.getElementById('login-password');

    if (role === 'superadmin' || role === 'admin') {
        cedulaInput.value = '9999999999';
        emailInput.value = 'superadmin@condo.com';
        passwordInput.value = 'admin123';
    } else {
        cedulaInput.value = '';
        emailInput.value = '';
        passwordInput.value = '';
    }
}

function onRecoverRoleChange() {
    clearLoginAlert();
    const role = document.getElementById('recover-role') ? document.getElementById('recover-role').value : 'superadmin';
    const cedulaInput = document.getElementById('recover-cedula');
    const emailInput = document.getElementById('recover-email');

    if (role === 'superadmin' || role === 'admin') {
        if (cedulaInput) cedulaInput.value = '9999999999';
        if (emailInput) emailInput.value = 'superadmin@condo.com';
    }
}

window.setLoginMode = setLoginMode;
window.onLoginRoleChange = onLoginRoleChange;
window.onRecoverRoleChange = onRecoverRoleChange;

async function handleLoginSubmit(e) {
    e.preventDefault();
    clearLoginAlert();
    const role = document.getElementById('login-role').value;
    const cedula = (document.getElementById('login-cedula').value || '').trim();
    const email = (document.getElementById('login-email').value || '').trim();
    const password = (document.getElementById('login-password').value || '').trim();

    if (!cedula || !email || !password) {
        showLoginAlert("Por favor completa todos los campos requeridos.", "error");
        return;
    }

    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cedula, email, role, password })
        });

        const data = await res.json();
        if (res.ok) {
            currentUser = data.user;
            localStorage.setItem('currentUser', JSON.stringify(currentUser));
            
            showToast(`¡Bienvenido, ${currentUser.name}!`);
            toggleLoginScreen(false);
            
            applyRoleAccess(currentUser);
            loadAllData();
            
            if (data.needs_password_change) {
                showToast("Has ingresado con clave temporal. Establece tu contraseña permanente.", "warning");
                document.getElementById('reset-cedula').value = currentUser.cedula;
                document.getElementById('reset-role').value = currentUser.role;
                document.getElementById('reset-temp-pin').value = password;
                toggleLoginScreen(true);
                setLoginMode('reset');
                showLoginAlert("Has ingresado con un PIN temporal. Por favor define tu nueva contraseña permanente a continuación.", "warning");
            }
        } else {
            const errorMsg = data.detail || "Credenciales incorrectas.";
            showLoginAlert(errorMsg, "error");
            showToast(errorMsg, "error");
        }
    } catch (err) {
        const errorMsg = "Error al conectar con el servidor: " + (err.message || err);
        showLoginAlert(errorMsg, "error");
        showToast(errorMsg, "error");
    }
}

async function handleRecoverSubmit(e) {
    e.preventDefault();
    clearLoginAlert();
    const role = document.getElementById('recover-role').value;
    const cedula = (document.getElementById('recover-cedula').value || '').trim();
    const email = (document.getElementById('recover-email').value || '').trim();

    if (!cedula || !email) {
        showLoginAlert("Por favor ingresa tu cédula y correo electrónico.", "error");
        return;
    }

    try {
        const res = await fetch('/api/auth/request-temp-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cedula, email, role })
        });

        const data = await res.json();
        if (res.ok) {
            if (data.simulated || data.temp_pin) {
                const pin = data.temp_pin || '';
                const alertHtml = `<strong>PIN Temporal Generado: <span style="font-size: 1.15rem; color: #60a5fa; letter-spacing: 2px; font-family: monospace;">${pin}</span></strong><br><span style="font-size: 0.85rem; color: #cbd5e1;">(Válido por 15 min). El correo no está configurado en el servidor. Usa este PIN directamente en el campo de contraseña para ingresar.</span>`;
                
                // Pre-fill fields on login form and reset form
                document.getElementById('login-role').value = role;
                document.getElementById('login-cedula').value = cedula;
                document.getElementById('login-email').value = email;
                document.getElementById('login-password').value = pin;
                
                document.getElementById('reset-cedula').value = cedula;
                document.getElementById('reset-role').value = role;
                document.getElementById('reset-temp-pin').value = pin;
                
                setLoginMode('login');
                showLoginAlert(alertHtml, "warning", true);
                showToast(`PIN Temporal: ${pin}`, "warning");
            } else {
                const msg = data.message || "Clave temporal enviada a tu correo registrado. Revisa tu bandeja de entrada o spam.";
                document.getElementById('login-role').value = role;
                document.getElementById('login-cedula').value = cedula;
                document.getElementById('login-email').value = email;
                setLoginMode('login');
                showLoginAlert(msg, "success");
                showToast("Clave temporal enviada a tu correo.");
            }
        } else {
            const errorMsg = data.detail || "Datos incorrectos. Verifique su cédula, correo y rol.";
            showLoginAlert(errorMsg, "error");
            showToast(errorMsg, "error");
        }
    } catch (err) {
        const errorMsg = "Error al conectar con el servidor: " + (err.message || err);
        showLoginAlert(errorMsg, "error");
        showToast(errorMsg, "error");
    }
}

async function handleResetSubmit(e) {
    e.preventDefault();
    clearLoginAlert();
    const cedula = (document.getElementById('reset-cedula').value || '').trim();
    const role = (document.getElementById('reset-role').value || '').trim();
    const temp_password = (document.getElementById('reset-temp-pin').value || '').trim();
    const new_password = (document.getElementById('reset-new-password').value || '').trim();

    if (!temp_password || !new_password) {
        showLoginAlert("Por favor ingresa el PIN temporal y la nueva contraseña.", "error");
        return;
    }
    if (new_password.length < 4) {
        showLoginAlert("La nueva contraseña debe tener al menos 4 caracteres.", "error");
        return;
    }

    try {
        const res = await fetch('/api/auth/reset-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cedula, role, temp_password, new_password })
        });

        const data = await res.json();
        if (res.ok) {
            const msg = data.message || "¡Contraseña actualizada exitosamente! Ya puedes iniciar sesión.";
            showToast(msg);
            setLoginMode('login');
            showLoginAlert(msg, "success");
            
            // Populate credentials to make it easy
            document.getElementById('login-role').value = role;
            document.getElementById('login-cedula').value = cedula;
            document.getElementById('login-password').value = new_password;
        } else {
            const errorMsg = data.detail || "Error al restablecer contraseña.";
            showLoginAlert(errorMsg, "error");
            showToast(errorMsg, "error");
        }
    } catch (err) {
        const errorMsg = "Error de comunicación: " + (err.message || err);
        showLoginAlert(errorMsg, "error");
        showToast(errorMsg, "error");
    }
}

function toggleProfileDropdown(e) {
    e.stopPropagation();
    const dropdown = document.getElementById('profile-dropdown');
    dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
}

function logoutUser() {
    localStorage.removeItem('currentUser');
    currentUser = null;
    document.getElementById('profile-dropdown').style.display = 'none';
    toggleLoginScreen(true);
    setLoginMode('login');
    onLoginRoleChange();
}

function applyRoleAccess(user) {
    // Top Nav menu selections
    const navResumen = document.getElementById('nav-resumen');
    const subDirectorio = document.getElementById('submenu-directorio');
    const subFinanzas = document.getElementById('submenu-finanzas');
    const subAjustes = document.getElementById('submenu-ajustes');
    const navMisFinanzas = document.getElementById('nav-mis-finanzas');
    const navAdmins = document.getElementById('nav-admins');
    
    // User profile widget
    if (document.getElementById('user-display-name')) {
        document.getElementById('user-display-name').innerText = user.name || 'Usuario';
    }
    
    // Default visibility toggles
    if (user.role === 'superadmin') {
        if (navResumen) navResumen.style.display = 'flex';
        if (subDirectorio) subDirectorio.style.display = 'flex';
        if (subFinanzas) subFinanzas.style.display = 'flex';
        if (subAjustes) subAjustes.style.display = 'flex';
        if (navMisFinanzas) navMisFinanzas.style.display = 'none';
        if (navAdmins) navAdmins.style.display = 'flex';
        if (document.getElementById('user-display-role')) {
            document.getElementById('user-display-role').innerText = 'Super Administrador';
        }
        
        switchTab('resumen');
    } else if (user.role === 'admin') {
        if (navResumen) navResumen.style.display = 'flex';
        if (subDirectorio) subDirectorio.style.display = 'flex';
        if (subFinanzas) subFinanzas.style.display = 'flex';
        if (subAjustes) subAjustes.style.display = 'flex';
        if (navMisFinanzas) navMisFinanzas.style.display = 'none';
        if (navAdmins) navAdmins.style.display = 'none';
        if (document.getElementById('user-display-role')) {
            document.getElementById('user-display-role').innerText = 'Administrador';
        }
        
        switchTab('resumen');
    } else if (user.role === 'owner' || user.role === 'tenant') {
        if (navResumen) navResumen.style.display = 'none';
        if (subDirectorio) subDirectorio.style.display = 'none';
        if (subFinanzas) subFinanzas.style.display = 'none';
        if (subAjustes) subAjustes.style.display = 'none';
        if (navMisFinanzas) navMisFinanzas.style.display = 'flex';
        if (navAdmins) navAdmins.style.display = 'none';
        
        const roleText = user.role === 'owner' ? 'Propietario' : 'Inquilino';
        if (document.getElementById('user-display-role')) {
            document.getElementById('user-display-role').innerText = `${roleText} Depto ${user.unit_id || ''}`;
        }
        
        switchTab('mis-finanzas');
    }
}

// Admins Management (Superadmin only)
async function registerAdmin(e) {
    e.preventDefault();
    const name = document.getElementById('admin-name').value;
    const cedula = document.getElementById('admin-cedula').value;
    const email = document.getElementById('admin-email').value;
    const password = document.getElementById('admin-password').value;

    try {
        const res = await fetch('/api/admin/register', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-User-Role': 'superadmin'
            },
            body: JSON.stringify({ name, cedula, email, password })
        });

        if (res.ok) {
            showToast("Administrador registrado exitosamente.");
            document.getElementById('form-register-admin').reset();
            loadAdmins();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al registrar administrador.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación.", "error");
    }
}

async function loadAdmins() {
    if (!currentUser || currentUser.role !== 'superadmin') return;
    
    try {
        const res = await fetch('/api/admins', {
            headers: { 'X-User-Role': 'superadmin' }
        });
        if (res.ok) {
            const list = await res.json();
            renderAdminsList(list);
        }
    } catch (err) {
        console.error("Error loading admins:", err);
    }
}

function renderAdminsList(list) {
    const tbody = document.getElementById('table-admins-list').querySelector('tbody');
    tbody.innerHTML = "";
    
    if (list.length === 0) {
        tbody.innerHTML = getTableEmptyStateHtml(4, 'users', 'No hay administradores registrados.');
        return;
    }
    
    list.forEach(a => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><b>${a.name}</b></td>
            <td>${a.cedula}</td>
            <td>${a.email}</td>
            <td><span class="status-badge paid">Activo</span></td>
        `;
        tbody.appendChild(row);
    });
}

// Condomino specific obligations list
function renderMyObligations() {
    if (!currentUser || (currentUser.role !== 'owner' && currentUser.role !== 'tenant')) return;
    
    const tbody = document.getElementById('table-my-obligations').querySelector('tbody');
    tbody.innerHTML = "";
    
    const unit_id = String(currentUser.unit_id);
    const currency = settingsData.currency || '$';
    
    // Get personal aliquots and additional charges
    const myAliquots = aliquotsData.filter(a => String(a.unit) === unit_id);
    const myCharges = additionalChargesData.filter(c => String(c.unit) === unit_id);
    
    let obligations = [];
    
    myAliquots.forEach(a => {
        obligations.push({
            id: a.id,
            concept: `Alícuota Ordinaria ${a.month} ${a.year}`,
            amount: a.amount,
            late_fee: a.late_fee,
            total: a.amount + a.late_fee,
            payment_date: a.payment_date || '-',
            reference: a.reference || '-',
            status: a.status,
            type: 'aliquot'
        });
    });
    
    myCharges.forEach(c => {
        obligations.push({
            id: c.id,
            concept: `${c.type}: ${c.description}`,
            amount: c.amount,
            late_fee: 0.0,
            total: c.amount,
            payment_date: c.payment_date || '-',
            reference: c.reference || '-',
            status: c.status,
            type: 'charge'
        });
    });
    
    // Sort pending first, then by date/id desc
    obligations.sort((a, b) => {
        if (a.status !== 'Pagado' && b.status === 'Pagado') return -1;
        if (a.status === 'Pagado' && b.status !== 'Pagado') return 1;
        return b.id.localeCompare(a.id);
    });
    
    if (obligations.length === 0) {
        tbody.innerHTML = getTableEmptyStateHtml(8, 'inbox', 'No tienes obligaciones de pago registradas.');
        return;
    }
    
    // Render rows
    obligations.forEach(o => {
        const row = document.createElement('tr');
        
        let badgeClass = 'pending';
        if (o.status === 'Pagado') badgeClass = 'paid';
        if (o.status === 'Validación Manual') badgeClass = 'validation';
        
        let actionBtnHtml = '';
        if (o.status === 'Pendiente') {
            actionBtnHtml = `<button class="btn btn-primary-sm" style="display:flex; align-items:center; gap:4px; padding:6px 10px;" onclick="openUploadMyReceipt('${o.id}', '${o.type}')"><i data-lucide="upload-cloud" style="width:12px;height:12px;"></i> Registrar Pago</button>`;
        } else if (o.status === 'Pagado') {
            actionBtnHtml = `<div style="display:flex; gap:4px; align-items:center;">
                <a href="/api/receipts/${o.type}/${o.id}/pdf" target="_blank" class="btn btn-secondary-sm" style="display:flex; align-items:center; gap:4px; padding:6px 10px;" title="Ver / Imprimir Recibo Oficial"><i data-lucide="printer" style="width:12px;height:12px;"></i> Recibo</a>
                <button class="btn btn-secondary-sm" style="display:flex; align-items:center; gap:4px; padding:6px 10px;" onclick="resendReceipt('${o.id}', '${o.type}')" title="Reenviar a mi correo"><i data-lucide="refresh-cw" style="width:12px;height:12px;"></i> Reenviar</button>
            </div>`;
        } else {
            actionBtnHtml = `<span style="font-size:0.8rem; color:var(--text-muted);">En proceso</span>`;
        }
        
        row.innerHTML = `
            <td><b>${o.concept}</b></td>
            <td>${currency}${o.amount.toFixed(2)}</td>
            <td>${currency}${o.late_fee.toFixed(2)}</td>
            <td><strong>${currency}${o.total.toFixed(2)}</strong></td>
            <td>${o.payment_date}</td>
            <td>${o.reference}</td>
            <td><span class="status-badge ${badgeClass}">${o.status}</span></td>
            <td>${actionBtnHtml}</td>
        `;
        tbody.appendChild(row);
    });
    
    lucide.createIcons();
    
    // Calculate total debt and update certificate status
    const totalDebt = obligations
        .filter(o => o.status !== 'Pagado')
        .reduce((sum, o) => sum + o.total, 0);
        
    const statusText = document.getElementById('cert-panel-status-text');
    const downloadBtn = document.getElementById('btn-download-cert');
    
    if (totalDebt > 0) {
        statusText.innerHTML = `Tienes un saldo pendiente de <strong>${currency}${totalDebt.toFixed(2)}</strong>. Por favor, regulariza tus pagos para habilitar el Certificado de No Adeudar.`;
        downloadBtn.disabled = true;
    } else {
        statusText.innerHTML = `🎉 ¡Excelente! Te encuentras al día en todas tus obligaciones de pago. Ya puedes descargar tu certificado oficial firmado.`;
        downloadBtn.disabled = false;
    }
}

function openUploadMyReceipt(id, type) {
    openModal('modal-upload-receipt-manual');
    document.getElementById('receipt-manual-unit').value = currentUser.unit_id;
    document.getElementById('receipt-manual-text').value = `Nombre: ${currentUser.name}\nDepartamento: ${currentUser.unit_id}\nConcepto: ${id}\nMes: ${id.split('-')[2] || 'Actual'}\nReferencia: (Ingresa código banco aquí)\nMonto Pagado:`;
}

function downloadMyCertificate() {
    if (!currentUser || !currentUser.unit_id) return;
    window.open(`/api/units/${currentUser.unit_id}/no-debt-certificate`, '_blank');
}

async function openAdminCertificateModal(preselectedUnitId = null) {
    const select = document.getElementById('cert-admin-unit-select');
    if (!select) return;
    
    if (!unitsData || unitsData.length === 0) {
        try {
            const res = await fetch('/api/units');
            if (res.ok) {
                unitsData = await res.json();
            }
        } catch (_) {}
    }
    
    select.innerHTML = '<option value="">-- Selecciona Departamento --</option>';
    
    if (unitsData && unitsData.length > 0) {
        unitsData.forEach(u => {
            const opt = document.createElement('option');
            opt.value = u.id;
            opt.innerText = `Depto ${u.id} - ${u.owner}`;
            select.appendChild(opt);
        });
    }
    
    if (preselectedUnitId) {
        select.value = preselectedUnitId;
        checkAdminCertStatus(preselectedUnitId);
    } else {
        const statusBox = document.getElementById('cert-admin-status-box');
        if (statusBox) statusBox.style.display = 'none';
        const downloadBtn = document.getElementById('btn-admin-download-cert');
        if (downloadBtn) downloadBtn.disabled = true;
    }
    
    openModal('modal-admin-certificate');
    lucide.createIcons();
}

function checkAdminCertStatus(unit_id) {
    const statusBox = document.getElementById('cert-admin-status-box');
    const downloadBtn = document.getElementById('btn-admin-download-cert');
    if (!unit_id) {
        if (statusBox) statusBox.style.display = 'none';
        if (downloadBtn) downloadBtn.disabled = true;
        return;
    }
    
    const currency = settingsData.currency || '$';
    const unit = unitsData.find(u => String(u.id) === String(unit_id));
    const ownerName = unit ? unit.owner : `Depto ${unit_id}`;
    
    // Calculate pending debt
    const pendingAliquots = aliquotsData.filter(a => String(a.unit) === String(unit_id) && a.status !== 'Pagado' && a.status !== 'Exonerado');
    const pendingCharges = additionalChargesData.filter(c => String(c.unit) === String(unit_id) && c.status !== 'Pagado' && c.status !== 'Exonerado');
    
    let totalDebt = 0;
    let debtDetails = [];
    
    pendingAliquots.forEach(a => {
        const amt = (a.amount + a.late_fee) - (a.paid_amount || 0);
        if (amt > 0.01) {
            totalDebt += amt;
            debtDetails.push(`Alícuota ${a.month} ${a.year}: ${currency}${amt.toFixed(2)}`);
        }
    });
    
    pendingCharges.forEach(c => {
        const amt = c.amount - (c.paid_amount || 0);
        if (amt > 0.01) {
            totalDebt += amt;
            debtDetails.push(`${c.type || 'Cargo'} (${c.description}): ${currency}${amt.toFixed(2)}`);
        }
    });
    
    if (statusBox) {
        statusBox.style.display = 'block';
        
        if (totalDebt > 0.01) {
            statusBox.innerHTML = `
                <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 14px;">
                    <div style="display: flex; align-items: center; gap: 8px; color: #f87171; font-weight: 600; font-size: 0.95rem;">
                        <i data-lucide="alert-triangle" style="width: 18px; height: 18px;"></i>
                        <span>Departamento en Mora - Deuda Total: ${currency}${totalDebt.toFixed(2)}</span>
                    </div>
                    <p style="font-size: 0.85rem; color: #cbd5e1; margin: 8px 0 6px 0;">
                        El <strong>Depto ${unit_id}</strong> (${ownerName}) posee obligaciones pendientes y <u>no califica</u> para la emisión del certificado de solvencia:
                    </p>
                    <ul style="margin: 0; padding-left: 20px; font-size: 0.82rem; color: #fca5a5;">
                        ${debtDetails.map(d => `<li>${d}</li>`).join('')}
                    </ul>
                </div>
            `;
            if (downloadBtn) downloadBtn.disabled = true;
        } else {
            statusBox.innerHTML = `
                <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 14px;">
                    <div style="display: flex; align-items: center; gap: 8px; color: #34d399; font-weight: 600; font-size: 0.95rem;">
                        <i data-lucide="check-circle" style="width: 18px; height: 18px;"></i>
                        <span>¡Departamento al Día! (Solvente)</span>
                    </div>
                    <p style="font-size: 0.85rem; color: #cbd5e1; margin: 8px 0 0 0;">
                        El <strong>Depto ${unit_id}</strong> (${ownerName}) no registra deudas ni alícuotas pendientes a la fecha. Califica para el <strong>Certificado Oficial de No Adeudar</strong>.
                    </p>
                </div>
            `;
            if (downloadBtn) downloadBtn.disabled = false;
        }
    }
    lucide.createIcons();
}

function downloadAdminCertificate() {
    const select = document.getElementById('cert-admin-unit-select');
    const unit_id = select ? select.value : null;
    if (!unit_id) {
        showToast("Por favor selecciona un departamento.", "warning");
        return;
    }
    window.open(`/api/units/${unit_id}/no-debt-certificate`, '_blank');
    showToast(`Descargando Certificado de No Adeudar para Depto ${unit_id}...`);
}

function emitAdminCertificate(unit_id) {
    const pendingAliquots = aliquotsData.filter(a => String(a.unit) === String(unit_id) && a.status !== 'Pagado' && a.status !== 'Exonerado');
    const pendingCharges = additionalChargesData.filter(c => String(c.unit) === String(unit_id) && c.status !== 'Pagado' && c.status !== 'Exonerado');
    
    let totalDebt = 0;
    pendingAliquots.forEach(a => {
        totalDebt += (a.amount + a.late_fee) - (a.paid_amount || 0);
    });
    pendingCharges.forEach(c => {
        totalDebt += c.amount - (c.paid_amount || 0);
    });
    
    if (totalDebt > 0.01) {
        openAdminCertificateModal(unit_id);
        showToast(`El Depto ${unit_id} registra valores pendientes de pago.`, 'warning');
    } else {
        window.open(`/api/units/${unit_id}/no-debt-certificate`, '_blank');
        showToast(`Certificado de No Adeudar generado para Depto ${unit_id}`, 'success');
    }
}

window.openAdminCertificateModal = openAdminCertificateModal;
window.checkAdminCertStatus = checkAdminCertStatus;
window.downloadAdminCertificate = downloadAdminCertificate;
window.emitAdminCertificate = emitAdminCertificate;

function toggleLoginScreen(showLogin) {
    const loginOverlay = document.getElementById('login-overlay');
    const appContainer = document.querySelector('.app-container');
    if (showLogin) {
        loginOverlay.style.display = 'flex';
        appContainer.style.display = 'none';
    } else {
        loginOverlay.style.display = 'none';
        appContainer.style.display = 'flex';
    }
}

async function confirmUndoPayment(id, type) {
    const term = type === 'aliquot' ? 'esta alícuota' : 'este cargo adicional';
    const confirmMsg = `¿Estás seguro de que deseas eliminar/deshacer el pago registrado para ${term}?\n\nLa obligación volverá al estado 'Pendiente', se eliminará el recibo PDF generado y la transferencia bancaria asociada volverá a estar disponible para su conciliación.`;
    
    if (!confirm(confirmMsg)) return;
    
    try {
        const res = await fetch('/api/undo-payment', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, type })
        });
        
        if (res.ok) {
            showToast("Pago eliminado/deshecho con éxito.");
            loadAllData();
        } else {
            const err = await res.json();
            showToast(err.detail || "Error al deshacer el pago.", "error");
        }
    } catch (err) {
        console.error("Error undoing payment:", err);
        showToast("Error de comunicación con el servidor.", "error");
    }
}

function renderInitialDebtsTable() {
    console.log("renderInitialDebtsTable: Start");
    const tbody = document.getElementById('table-initial-debts-body');
    if (!tbody) {
        console.warn("renderInitialDebtsTable: tbody element not found in DOM");
        return;
    }
    tbody.innerHTML = "";
    const currency = settingsData.currency || '$';

    if (!additionalChargesData) {
        console.warn("renderInitialDebtsTable: additionalChargesData is null/undefined");
        return;
    }

    console.log("renderInitialDebtsTable: all charges:", additionalChargesData);
    const initialDebts = additionalChargesData.filter(c => c.type === 'Deuda Inicial');
    console.log("renderInitialDebtsTable: filtered initial debts:", initialDebts);

    if (initialDebts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center" style="color:var(--text-muted); padding:10px;">No hay deudas iniciales registradas.</td></tr>`;
        return;
    }

    initialDebts.forEach(c => {
        const row = document.createElement('tr');
        
        let statusClass = "pending";
        let statusText = "Pendiente";
        if (c.status === 'Pagado') {
            statusClass = "paid";
            statusText = "Pagado";
        } else if (c.status === 'Validación Manual') {
            statusClass = "manual";
            statusText = "Val. Manual";
        }
        
        const amt = Number(c.amount) || 0;
        row.innerHTML = `
            <td><b>Depto ${c.unit}</b></td>
            <td>${currency}${amt.toFixed(2)}</td>
            <td>${c.description}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            <td>
                <div style="display:flex; gap:6px;">
                    <button class="btn btn-secondary-sm" onclick="editInitialDebt('${c.id}')" style="padding:4px 8px; font-size:0.75rem;"><i data-lucide="edit" style="width:12px;height:12px;"></i></button>
                    <button class="btn btn-secondary-sm" onclick="deleteInitialDebt('${c.id}')" style="padding:4px 8px; font-size:0.75rem; background:#ef4444; border-color:#ef4444; color:white;"><i data-lucide="trash-2" style="width:12px;height:12px;"></i></button>
                </div>
            </td>
        `;
        tbody.appendChild(row);
    });
    lucide.createIcons();
}

function editInitialDebt(id) {
    const c = additionalChargesData.find(x => String(x.id) === String(id));
    if (!c) return;
    
    document.getElementById('edit-charge-id').value = c.id;
    document.getElementById('edit-charge-unit').value = `Depto ${c.unit}`;
    document.getElementById('edit-charge-amount').value = c.amount;
    document.getElementById('edit-charge-description').value = c.description;
    document.getElementById('edit-charge-reference').value = c.reference || '';
    
    openModal('modal-edit-charge');
}

async function deleteInitialDebt(id) {
    if (!confirm("¿Está seguro de que desea eliminar este cobro/deuda?")) return;
    
    try {
        const res = await fetch(`/api/additional-charges/${id}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            showToast("Cobro eliminado correctamente.");
            loadAllData();
        } else {
            showToast("Error al eliminar el cobro.", "error");
        }
    } catch (err) {
        showToast("Error de conexión.", "error");
    }
}

async function handleEditChargeSubmit(e) {
    e.preventDefault();
    const id = document.getElementById('edit-charge-id').value;
    const amount = parseFloat(document.getElementById('edit-charge-amount').value);
    const description = document.getElementById('edit-charge-description').value;
    const reference = document.getElementById('edit-charge-reference').value.trim();
    
    if (isNaN(amount) || amount <= 0) {
        showToast("Por favor ingresa un monto válido.", "error");
        return;
    }

    try {
        const res = await fetch(`/api/additional-charges/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                amount, 
                description, 
                reference: reference || null,
                auto_reconcile: true 
            })
        });
        
        const data = await res.json();
        if (res.ok) {
            if (data.reconciled) {
                showToast(data.message || "Cargo conciliado automáticamente con el estado de cuenta.", "success");
            } else {
                showToast(data.message || "Cargo actualizado correctamente.");
            }
            closeModal('modal-edit-charge');
            loadAllData();
        } else {
            showToast(data.detail || "Error al actualizar el cargo.", "error");
        }
    } catch (err) {
        showToast("Error de conexión.", "error");
    }
}

async function handleSaveDatabaseSettings(e) {
    e.preventDefault();
    const db_type = document.getElementById('config-db-type').value;
    const db_host = document.getElementById('config-db-host').value;
    const db_port = parseInt(document.getElementById('config-db-port').value) || 5432;
    const db_user = document.getElementById('config-db-user').value;
    const db_password = document.getElementById('config-db-password').value;
    const db_name = document.getElementById('config-db-name').value;
    const db_custom_url = document.getElementById('config-db-custom-url').value;
    
    showToast("Guardando y conectando base de datos...", "warning");
    
    const res = await saveSettings({ db_type, db_host, db_port, db_user, db_password, db_name, db_custom_url });
    if (res && res.ok) {
        showToast("Configuración de base de datos aplicada correctamente.");
        loadAllData();
    } else if (res) {
        let errorMsg = "Error al guardar o conectar con la base de datos.";
        try {
            const err = await res.json();
            if (err && err.detail) errorMsg = err.detail;
        } catch (_) {}
        showToast(errorMsg, "error");
    } else {
        showToast("Error de conexión con el servidor.", "error");
    }
}

function toggleDatabaseSettingsVisibility() {
    const type = document.getElementById('config-db-type').value;
    const postgresGroup = document.getElementById('settings-group-postgres');
    if (postgresGroup) {
        postgresGroup.style.display = type === 'postgres' ? 'block' : 'none';
    }
}

function openEditAliquotModal(id, unit, period, amount, reference, paymentDate) {
    const a = aliquotsData.find(x => String(x.id) === String(id));
    document.getElementById('edit-aliquot-id').value = id;
    document.getElementById('edit-aliquot-unit').value = `Depto ${unit || (a ? a.unit : '')}`;
    document.getElementById('edit-aliquot-period').value = period || (a ? `${a.month} ${a.year}` : '');
    const baseAmount = (a && a.paid_amount > 0 && a.status !== 'Pagado') ? (Number(a.amount || 0) + Number(a.paid_amount || 0)) : (amount !== undefined ? amount : (a ? a.amount : 0));
    document.getElementById('edit-aliquot-amount').value = parseFloat(baseAmount).toFixed(2);
    document.getElementById('edit-aliquot-late-fee').value = parseFloat(a && a.late_fee !== undefined ? a.late_fee : 0).toFixed(2);
    document.getElementById('edit-aliquot-reference').value = (reference !== undefined ? reference : (a && a.reference ? a.reference : '')) || '';
    document.getElementById('edit-aliquot-payment-date').value = (paymentDate !== undefined ? paymentDate : (a && a.payment_date ? a.payment_date : '')) || '';
    openModal('modal-edit-aliquot');
}

async function handleEditAliquotSubmit(e) {
    e.preventDefault();
    const id = document.getElementById('edit-aliquot-id').value;
    const amount = parseFloat(document.getElementById('edit-aliquot-amount').value);
    const reference = document.getElementById('edit-aliquot-reference').value.trim();
    const payment_date = document.getElementById('edit-aliquot-payment-date').value;
    const late_fee_input = document.getElementById('edit-aliquot-late-fee');
    const late_fee = (late_fee_input && late_fee_input.value !== '' && !isNaN(parseFloat(late_fee_input.value))) ? parseFloat(late_fee_input.value) : null;

    if (isNaN(amount) || amount < 0) {
        showToast("Por favor ingresa un monto válido.", "error");
        return;
    }
    try {
        const res = await fetch(`/api/aliquots/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                amount,
                reference: reference || null,
                payment_date: payment_date || null,
                late_fee: late_fee,
                auto_reconcile: true
            })
        });
        const data = await res.json();
        if (res.ok) {
            if (data.reconciled) {
                showToast(data.message || "Referencia guardada y alícuota conciliada automáticamente.", "success");
            } else {
                showToast(data.message || "Alícuota actualizada correctamente.");
            }
            closeModal('modal-edit-aliquot');
            loadAllData();
        } else {
            showToast(data.detail || "Error al actualizar la alícuota.", "error");
        }
    } catch (err) {
        showToast("Error de conexión.", "error");
    }
}

function openEditBankTransactionModal(reference, date, amount, detail) {
    document.getElementById('edit-bank-old-reference').value = reference;
    document.getElementById('edit-bank-reference').value = reference;
    document.getElementById('edit-bank-date').value = date || '';
    document.getElementById('edit-bank-amount').value = parseFloat(amount || 0).toFixed(2);
    document.getElementById('edit-bank-detail').value = detail || '';
    openModal('modal-edit-bank-transaction');
}

async function handleEditBankTransactionSubmit(e) {
    e.preventDefault();
    const oldReference = document.getElementById('edit-bank-old-reference').value;
    const newReference = document.getElementById('edit-bank-reference').value.trim();
    const date = document.getElementById('edit-bank-date').value;
    const amount = parseFloat(document.getElementById('edit-bank-amount').value);
    const detail = document.getElementById('edit-bank-detail').value.trim();

    if (!newReference) {
        showToast("Por favor ingresa un número de referencia válido.", "error");
        return;
    }
    if (isNaN(amount) || amount <= 0) {
        showToast("Por favor ingresa un monto válido.", "error");
        return;
    }

    try {
        const res = await fetch(`/api/bank-statement/${encodeURIComponent(oldReference)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                reference: newReference,
                date,
                amount,
                detail
            })
        });

        const data = await res.json();
        if (res.ok) {
            if (data.reconciled) {
                showToast(data.message || "Transacción guardada y conciliada automáticamente.", "success");
            } else {
                showToast(data.message || "Transacción bancaria actualizada correctamente.");
            }
            closeModal('modal-edit-bank-transaction');
            loadAllData();
        } else {
            showToast(data.detail || "Error al actualizar la transacción bancaria.", "error");
        }
    } catch (err) {
        showToast("Error de comunicación con el servidor.", "error");
    }
}

// ==========================================================================
// REPORTES FINANCIEROS MODULE
// ==========================================================================
let currentReportSubtype = 'payments'; // 'payments' | 'debtors' | 'unreconciled'
let currentReportData = null;

function initReportesTab() {
    // Set default dates if empty: first day of current year to today
    const now = new Date();
    const firstDayOfYear = new Date(now.getFullYear(), 0, 1).toISOString().split('T')[0];
    const todayStr = now.toISOString().split('T')[0];
    
    const startEl = document.getElementById('rep-filter-start-date');
    const endEl = document.getElementById('rep-filter-end-date');
    if (startEl && !startEl.value) startEl.value = firstDayOfYear;
    if (endEl && !endEl.value) endEl.value = todayStr;
    
    // Ensure correct controls are visible
    updateReportFilterControls();
    loadReportPreview();
}

function switchReportSubtype(type) {
    currentReportSubtype = type;
    
    // Update button active state
    ['payments', 'debtors', 'unreconciled'].forEach(t => {
        const btn = document.getElementById(`btn-tab-rep-${t}`);
        if (btn) {
            if (t === type) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        }
    });

    updateReportFilterControls();
    loadReportPreview();
}

function updateReportFilterControls() {
    const startDateGroup = document.getElementById('filter-group-start-date');
    const endDateGroup = document.getElementById('filter-group-end-date');
    const unitGroup = document.getElementById('filter-group-unit');
    const dynamicGroup = document.getElementById('filter-group-dynamic');
    const dynamicLabel = document.getElementById('rep-filter-dynamic-label');
    const dynamicSelect = document.getElementById('rep-filter-dynamic');
    const titleEl = document.getElementById('report-table-title');
    const subtitleEl = document.getElementById('report-table-subtitle');

    if (currentReportSubtype === 'payments') {
        if (startDateGroup) startDateGroup.style.display = 'flex';
        if (endDateGroup) endDateGroup.style.display = 'flex';
        if (unitGroup) unitGroup.style.display = 'flex';
        if (dynamicGroup) dynamicGroup.style.display = 'flex';
        if (dynamicLabel) dynamicLabel.innerText = 'Categoría';
        if (dynamicSelect) {
            dynamicSelect.innerHTML = `
                <option value="">Todas las categorías</option>
                <option value="Alicuota">Alícuota</option>
                <option value="Abono">Abono</option>
                <option value="Multa / Cargo">Multa / Cargo</option>
                <option value="Otro Ingreso">Otro Ingreso</option>
            `;
        }
        if (titleEl) titleEl.innerText = 'Reporte de Pagos y Recaudación';
        if (subtitleEl) subtitleEl.innerText = 'Desglose detallado de todos los ingresos percibidos en el período.';
    } else if (currentReportSubtype === 'debtors') {
        if (startDateGroup) startDateGroup.style.display = 'none';
        if (endDateGroup) endDateGroup.style.display = 'none';
        if (unitGroup) unitGroup.style.display = 'flex';
        if (dynamicGroup) dynamicGroup.style.display = 'flex';
        if (dynamicLabel) dynamicLabel.innerText = 'Estado de Cartera';
        if (dynamicSelect) {
            dynamicSelect.innerHTML = `
                <option value="debtors_only">Solo con Deuda (> $0.00)</option>
                <option value="all">Todos los Departamentos</option>
                <option value="up_to_date">Al Día ($0.00)</option>
            `;
        }
        if (titleEl) titleEl.innerText = 'Reporte de Deudores y Cartera Vencida';
        if (subtitleEl) subtitleEl.innerText = 'Consolidado de obligaciones pendientes, recargos por mora y niveles de morosidad.';
    } else if (currentReportSubtype === 'unreconciled') {
        if (startDateGroup) startDateGroup.style.display = 'flex';
        if (endDateGroup) endDateGroup.style.display = 'flex';
        if (unitGroup) unitGroup.style.display = 'none';
        if (dynamicGroup) dynamicGroup.style.display = 'flex';
        if (dynamicLabel) dynamicLabel.innerText = 'Tipo de Registro';
        if (dynamicSelect) {
            dynamicSelect.innerHTML = `
                <option value="">Todos los Pendientes</option>
                <option value="Comprobante Residente">Comprobantes de Residentes (Validación Manual)</option>
                <option value="Movimiento Bancario">Movimientos Bancarios Sin Conciliar</option>
            `;
        }
        if (titleEl) titleEl.innerText = 'Reporte de Comprobantes y Movimientos Sin Conciliar';
        if (subtitleEl) subtitleEl.innerText = 'Comprobantes pendientes de validación administrativa y transacciones bancarias huérfanas.';
    }
}

function getReportFilterParams() {
    const params = new URLSearchParams();
    params.set('report_type', currentReportSubtype);

    const startDate = document.getElementById('rep-filter-start-date')?.value;
    const endDate = document.getElementById('rep-filter-end-date')?.value;
    const unit = document.getElementById('rep-filter-unit')?.value;
    const dynamicVal = document.getElementById('rep-filter-dynamic')?.value;

    if (currentReportSubtype === 'payments') {
        if (startDate) params.set('start_date', startDate);
        if (endDate) params.set('end_date', endDate);
        if (unit) params.set('unit', unit);
        if (dynamicVal) params.set('category', dynamicVal);
    } else if (currentReportSubtype === 'debtors') {
        if (unit) params.set('unit', unit);
        if (dynamicVal) params.set('status_filter', dynamicVal);
    } else if (currentReportSubtype === 'unreconciled') {
        if (startDate) params.set('start_date', startDate);
        if (endDate) params.set('end_date', endDate);
        if (dynamicVal) params.set('record_type', dynamicVal);
    }
    return params;
}

async function loadReportPreview() {
    const tbody = document.getElementById('report-tbody');
    const recordsCount = document.getElementById('report-records-count');
    
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding: 35px; color: var(--text-muted);"><i data-lucide="loader-2" class="spin" style="width:24px;height:24px;display:inline-block;vertical-align:middle;margin-right:8px;"></i> Cargando vista previa del reporte...</td></tr>`;
        lucide.createIcons();
    }

    try {
        const params = getReportFilterParams();
        const res = await fetch(`/api/reports/preview?${params.toString()}`);
        if (!res.ok) {
            throw new Error(`Error ${res.status}: ${res.statusText}`);
        }
        const data = await res.json();
        currentReportData = data;
        
        // Render KPIs
        renderReportKPIs(data.kpis, currentReportSubtype);
        
        // Render Table
        renderReportTable(data, currentReportSubtype);
    } catch (err) {
        console.error("Error loading report preview:", err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center text-red" style="padding: 30px;">Error al cargar datos del reporte: ${err.message}</td></tr>`;
        }
    }
}

function renderReportKPIs(kpis, type) {
    const container = document.getElementById('report-kpis-grid');
    if (!container || !kpis) return;

    if (type === 'payments') {
        const totalGlobal = parseFloat(kpis.total_global || kpis.total_collected || kpis.total_amount || 0);
        const totalConciliado = parseFloat(kpis.total_reconciled || 0);
        const totalNoConciliado = parseFloat(kpis.total_unreconciled || 0);
        const countConciliados = kpis.count_reconciled || 0;
        const countNoConciliados = kpis.count_unreconciled || 0;
        const totalCount = kpis.count_payments || kpis.total_count || 0;

        container.innerHTML = `
            <div class="kpi-card gradient-blue">
                <div class="kpi-icon"><i data-lucide="dollar-sign"></i></div>
                <div class="kpi-content">
                    <h3>Total Global</h3>
                    <p class="kpi-value">$${totalGlobal.toFixed(2)}</p>
                    <span class="kpi-label">${totalCount} movimiento(s) en total</span>
                </div>
            </div>
            <div class="kpi-card gradient-emerald">
                <div class="kpi-icon"><i data-lucide="check-circle"></i></div>
                <div class="kpi-content">
                    <h3>Total Conciliado</h3>
                    <p class="kpi-value">$${totalConciliado.toFixed(2)}</p>
                    <span class="kpi-label">${countConciliados} pago(s) confirmados</span>
                </div>
            </div>
            <div class="kpi-card gradient-orange">
                <div class="kpi-icon"><i data-lucide="alert-circle"></i></div>
                <div class="kpi-content">
                    <h3>No Conciliados (Banco)</h3>
                    <p class="kpi-value text-orange">$${totalNoConciliado.toFixed(2)}</p>
                    <span class="kpi-label">${countNoConciliados} depósito(s) en estado de cuenta</span>
                </div>
            </div>
            <div class="kpi-card gradient-purple">
                <div class="kpi-icon"><i data-lucide="wallet"></i></div>
                <div class="kpi-content">
                    <h3>Alícuotas / Otros</h3>
                    <p class="kpi-value">$${(parseFloat(kpis.total_aliquots || 0) + parseFloat(kpis.total_charges || 0) + parseFloat(kpis.total_other_incomes || 0)).toFixed(2)}</p>
                    <span class="kpi-label">Alíc: $${parseFloat(kpis.total_aliquots || 0).toFixed(2)} | Multas: $${parseFloat(kpis.total_charges || 0).toFixed(2)}</span>
                </div>
            </div>
        `;
    } else if (type === 'debtors') {
        container.innerHTML = `
            <div class="kpi-card gradient-red">
                <div class="kpi-icon"><i data-lucide="alert-triangle"></i></div>
                <div class="kpi-content">
                    <h3>Cartera Vencida Total</h3>
                    <p class="kpi-value text-red">$${parseFloat(kpis.total_debt || 0).toFixed(2)}</p>
                    <span class="kpi-label">${kpis.total_debtors_count || 0} depto(s) en mora</span>
                </div>
            </div>
            <div class="kpi-card gradient-purple">
                <div class="kpi-icon"><i data-lucide="calendar"></i></div>
                <div class="kpi-content">
                    <h3>Alícuotas Impagas</h3>
                    <p class="kpi-value">$${parseFloat(kpis.total_unpaid_aliquots || 0).toFixed(2)}</p>
                    <span class="kpi-label">Capital pendiente</span>
                </div>
            </div>
            <div class="kpi-card gradient-orange">
                <div class="kpi-icon"><i data-lucide="percent"></i></div>
                <div class="kpi-content">
                    <h3>Recargos por Mora</h3>
                    <p class="kpi-value text-orange">$${parseFloat(kpis.total_late_fees || 0).toFixed(2)}</p>
                    <span class="kpi-label">Multas automáticas</span>
                </div>
            </div>
            <div class="kpi-card gradient-blue">
                <div class="kpi-icon"><i data-lucide="plus-circle"></i></div>
                <div class="kpi-content">
                    <h3>Cargos / Saldos Ant.</h3>
                    <p class="kpi-value">$${parseFloat((kpis.total_charges || 0) + (kpis.total_initial_debt || 0)).toFixed(2)}</p>
                    <span class="kpi-label">Cargos: $${parseFloat(kpis.total_charges || 0).toFixed(2)} | Inicial: $${parseFloat(kpis.total_initial_debt || 0).toFixed(2)}</span>
                </div>
            </div>
        `;
    } else if (type === 'unreconciled') {
        container.innerHTML = `
            <div class="kpi-card gradient-orange">
                <div class="kpi-icon"><i data-lucide="help-circle"></i></div>
                <div class="kpi-content">
                    <h3>Total No Conciliado</h3>
                    <p class="kpi-value text-orange">$${parseFloat(kpis.total_unreconciled_amount || 0).toFixed(2)}</p>
                    <span class="kpi-label">${kpis.total_pending_count || 0} registros pendientes</span>
                </div>
            </div>
            <div class="kpi-card gradient-blue">
                <div class="kpi-icon"><i data-lucide="receipt"></i></div>
                <div class="kpi-content">
                    <h3>Comprobantes Residentes</h3>
                    <p class="kpi-value">$${parseFloat(kpis.resident_receipts_amount || 0).toFixed(2)}</p>
                    <span class="kpi-label">${kpis.resident_receipts_count || 0} pendientes de validación</span>
                </div>
            </div>
            <div class="kpi-card gradient-red">
                <div class="kpi-icon"><i data-lucide="wallet-cards"></i></div>
                <div class="kpi-content">
                    <h3>Movimientos Banco</h3>
                    <p class="kpi-value">$${parseFloat(kpis.bank_tx_amount || 0).toFixed(2)}</p>
                    <span class="kpi-label">${kpis.bank_tx_count || 0} depósitos sin conciliar</span>
                </div>
            </div>
        `;
    }
    lucide.createIcons();
}

function renderReportTable(data, type) {
    const thead = document.getElementById('report-thead');
    const tbody = document.getElementById('report-tbody');
    const tfoot = document.getElementById('report-tfoot');
    const recordsCount = document.getElementById('report-records-count');
    const searchInput = document.getElementById('report-quick-search');
    if (searchInput) searchInput.value = '';

    const records = data.records || [];
    if (recordsCount) recordsCount.innerText = `${records.length} registro(s)`;

    if (type === 'payments') {
        if (thead) {
            thead.innerHTML = `
                <tr>
                    <th>Fecha</th>
                    <th>Depto</th>
                    <th>Pagador / Detalle</th>
                    <th>Concepto</th>
                    <th>Categoría</th>
                    <th>Referencia</th>
                    <th>Estado</th>
                    <th style="text-align: right;">Monto</th>
                </tr>
            `;
        }

        if (records.length === 0) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding: 30px; color: var(--text-muted);">No se encontraron pagos registrados con los filtros seleccionados.</td></tr>`;
            if (tfoot) tfoot.innerHTML = '';
            return;
        }

        let totalRecaudado = 0;
        let totalConciliado = 0;
        let totalNoConciliado = 0;
        let countConciliados = 0;
        let countNoConciliados = 0;

        let html = '';
        records.forEach(r => {
            const amt = parseFloat(r.amount || 0);
            totalRecaudado += amt;
            const isUnreconciled = (r.is_reconciled === false) || (r.status === 'No Conciliado') || (r.category && r.category.toLowerCase().includes('no conciliado'));

            if (isUnreconciled) {
                totalNoConciliado += amt;
                countNoConciliados++;
            } else {
                totalConciliado += amt;
                countConciliados++;
            }

            let catBadge = 'background: rgba(99, 102, 241, 0.15); color: #818cf8;';
            const catLower = (r.category || '').toLowerCase();
            if (catLower.includes('alícuota') || catLower.includes('alicuota')) catBadge = 'background: rgba(16, 185, 129, 0.15); color: #34d399;';
            else if (catLower.includes('abono')) catBadge = 'background: rgba(59, 130, 246, 0.15); color: #60a5fa;';
            else if (catLower.includes('multa') || catLower.includes('cargo') || catLower.includes('extra') || catLower.includes('deuda')) catBadge = 'background: rgba(245, 158, 11, 0.15); color: #fbbf24;';
            else if (catLower.includes('otro')) catBadge = 'background: rgba(168, 85, 247, 0.15); color: #c084fc;';
            else if (isUnreconciled) catBadge = 'background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4);';

            let statusBadge = `<span class="badge" style="background: rgba(16, 185, 129, 0.15); color: #34d399;">${r.status || 'Pagado'}</span>`;
            if (isUnreconciled) {
                statusBadge = `<span class="badge" style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; font-weight: 600;"><i data-lucide="help-circle" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:3px;"></i> No Conciliado</span>`;
            } else if (r.status === 'Abono Parcial') {
                statusBadge = `<span class="badge" style="background: rgba(59, 130, 246, 0.15); color: #60a5fa;">Abono Parcial</span>`;
            }

            const unitDisplay = (r.unit && r.unit !== 'N/A' && r.unit !== '- / Banco') ? `<strong>Depto ${r.unit}</strong>` : (r.unit === '- / Banco' ? '<span style="color: #fbbf24; font-size: 0.85rem;"><i data-lucide="building" style="width:12px;height:12px;display:inline-block;vertical-align:middle;"></i> Banco / S/C</span>' : 'General');

            html += `
                <tr class="report-row" style="${isUnreconciled ? 'background: rgba(245, 158, 11, 0.03);' : ''}">
                    <td>${r.date || '-'}</td>
                    <td>${unitDisplay}</td>
                    <td>${r.payer || '-'}</td>
                    <td>${r.concept || '-'}</td>
                    <td><span class="badge" style="${catBadge}">${r.category || '-'}</span></td>
                    <td><code>${r.reference || '-'}</code></td>
                    <td>${statusBadge}</td>
                    <td style="text-align: right; font-weight: 600; color: ${isUnreconciled ? '#fbbf24' : '#34d399'};">$${amt.toFixed(2)}</td>
                </tr>
            `;
        });
        if (tbody) tbody.innerHTML = html;

        if (tfoot) {
            tfoot.innerHTML = `
                <tr class="report-table-footer-subtotals" style="border-top: 2px solid var(--border-color, #334155); background: rgba(15, 23, 42, 0.4);">
                    <td colspan="7" style="text-align: right; font-weight: 600; color: var(--text-muted, #94a3b8);">Subtotal Conciliados (${countConciliados} registros):</td>
                    <td style="text-align: right; font-weight: 600; color: #34d399;">$${totalConciliado.toFixed(2)}</td>
                </tr>
                <tr class="report-table-footer-subtotals" style="background: rgba(245, 158, 11, 0.08);">
                    <td colspan="7" style="text-align: right; font-weight: 600; color: #fbbf24;">Subtotal No Conciliados en Banco (${countNoConciliados} registros):</td>
                    <td style="text-align: right; font-weight: 600; color: #fbbf24;">$${totalNoConciliado.toFixed(2)}</td>
                </tr>
                <tr class="report-table-footer-totals" style="background: rgba(30, 41, 59, 0.7); border-top: 1px solid var(--border-color, #475569);">
                    <td colspan="7" style="text-align: right; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #ffffff;">Gran Total Global:</td>
                    <td style="text-align: right; font-weight: 700; font-size: 1.1rem; color: #38bdf8;">$${totalRecaudado.toFixed(2)}</td>
                </tr>
            `;
        }
    } else if (type === 'debtors') {
        if (thead) {
            thead.innerHTML = `
                <tr>
                    <th>Depto</th>
                    <th>Propietario / Residente</th>
                    <th>Meses en Mora</th>
                    <th style="text-align: right;">Alícuotas</th>
                    <th style="text-align: right;">Multas / Cargos</th>
                    <th style="text-align: right;">Saldo Anterior</th>
                    <th style="text-align: right;">Total Deuda</th>
                    <th style="text-align: center;">Nivel de Mora</th>
                </tr>
            `;
        }

        if (records.length === 0) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding: 30px; color: var(--text-muted);">No hay registros de cartera vencida con los filtros seleccionados.</td></tr>`;
            if (tfoot) tfoot.innerHTML = '';
            return;
        }

        let totAlicuotas = 0;
        let totMultas = 0;
        let totInicial = 0;
        let totDeuda = 0;
        let html = '';

        records.forEach(r => {
            const alic = parseFloat(r.unpaid_aliquots_amount || 0);
            const multas = parseFloat((r.late_fees_amount || 0) + (r.additional_charges_amount || 0));
            const ini = parseFloat(r.initial_debt || 0);
            const total = parseFloat(r.total_debt || 0);

            totAlicuotas += alic;
            totMultas += multas;
            totInicial += ini;
            totDeuda += total;

            let morosityBadge = '';
            const months = r.unpaid_months_count || 0;
            if (total <= 0) {
                morosityBadge = '<span class="badge" style="background: rgba(16, 185, 129, 0.15); color: #34d399;">Al Día</span>';
            } else if (months <= 1) {
                morosityBadge = '<span class="badge" style="background: rgba(245, 158, 11, 0.15); color: #fbbf24;">Mora Leve (1 mes)</span>';
            } else if (months <= 3) {
                morosityBadge = `<span class="badge" style="background: rgba(239, 68, 68, 0.2); color: #f87171;">Mora Media (${months} meses)</span>`;
            } else {
                morosityBadge = `<span class="badge" style="background: rgba(239, 68, 68, 0.4); color: #fca5a5;">Mora Crítica (${months} meses)</span>`;
            }

            html += `
                <tr class="report-row">
                    <td><strong>Depto ${r.unit}</strong></td>
                    <td>${r.owner || '-'}</td>
                    <td>${months > 0 ? months + ' mes(es) [' + (r.unpaid_months_list || []).join(', ') + ']' : 'Ninguno'}</td>
                    <td style="text-align: right;">$${alic.toFixed(2)}</td>
                    <td style="text-align: right;">$${multas.toFixed(2)}</td>
                    <td style="text-align: right;">$${ini.toFixed(2)}</td>
                    <td style="text-align: right; font-weight: 700; color: ${total > 0 ? '#fb7185' : '#34d399'};">$${total.toFixed(2)}</td>
                    <td style="text-align: center;">${morosityBadge}</td>
                </tr>
            `;
        });
        if (tbody) tbody.innerHTML = html;

        if (tfoot) {
            tfoot.innerHTML = `
                <tr class="report-table-footer-totals">
                    <td colspan="3" style="text-align: right; font-weight: 700; text-transform: uppercase;">Totales Generales:</td>
                    <td style="text-align: right; font-weight: 700;">$${totAlicuotas.toFixed(2)}</td>
                    <td style="text-align: right; font-weight: 700;">$${totMultas.toFixed(2)}</td>
                    <td style="text-align: right; font-weight: 700;">$${totInicial.toFixed(2)}</td>
                    <td style="text-align: right; font-weight: 700; font-size: 1.05rem; color: #fb7185;">$${totDeuda.toFixed(2)}</td>
                    <td></td>
                </tr>
            `;
        }
    } else if (type === 'unreconciled') {
        if (thead) {
            thead.innerHTML = `
                <tr>
                    <th>Origen / Tipo</th>
                    <th>Fecha</th>
                    <th>Depto / Contacto</th>
                    <th>Detalle / Concepto</th>
                    <th>Referencia</th>
                    <th>Banco / Método</th>
                    <th style="text-align: right;">Monto</th>
                    <th>Estado</th>
                </tr>
            `;
        }

        if (records.length === 0) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding: 30px; color: var(--text-muted);">¡Excelente! No hay comprobantes ni movimientos bancarios pendientes de conciliación.</td></tr>`;
            if (tfoot) tfoot.innerHTML = '';
            return;
        }

        let totPendiente = 0;
        let html = '';

        records.forEach(r => {
            const amt = parseFloat(r.amount || 0);
            totPendiente += amt;

            const isResident = r.record_type === 'Comprobante Residente';
            const originBadge = isResident ? 
                '<span class="badge" style="background: rgba(99, 102, 241, 0.2); color: #a5b4fc;"><i data-lucide="file-text" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:4px;"></i> Comprobante Residente</span>' :
                '<span class="badge" style="background: rgba(244, 63, 94, 0.2); color: #fda4af;"><i data-lucide="wallet-cards" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:4px;"></i> Movimiento Bancario</span>';

            html += `
                <tr class="report-row">
                    <td>${originBadge}</td>
                    <td>${r.date || '-'}</td>
                    <td>${r.unit ? '<strong>Depto ' + r.unit + '</strong>' : (r.person_name || 'Sin asignar')}</td>
                    <td>${r.description || r.concept || '-'}</td>
                    <td><code>${r.reference || '-'}</code></td>
                    <td>${r.bank_or_method || '-'}</td>
                    <td style="text-align: right; font-weight: 600; color: #fbbf24;">$${amt.toFixed(2)}</td>
                    <td><span class="badge" style="background: rgba(245, 158, 11, 0.15); color: #fbbf24;">${r.status || 'Pendiente'}</span></td>
                </tr>
            `;
        });
        if (tbody) tbody.innerHTML = html;

        if (tfoot) {
            tfoot.innerHTML = `
                <tr class="report-table-footer-totals">
                    <td colspan="6" style="text-align: right; font-weight: 700; text-transform: uppercase;">Total Pendiente de Conciliar:</td>
                    <td style="text-align: right; font-weight: 700; font-size: 1.05rem; color: #fbbf24;">$${totPendiente.toFixed(2)}</td>
                    <td></td>
                </tr>
            `;
        }
        lucide.createIcons();
    }
}

function filterReportTableClientSide() {
    const query = (document.getElementById('report-quick-search')?.value || '').toLowerCase();
    const rows = document.querySelectorAll('#report-tbody tr.report-row');
    let visibleCount = 0;
    rows.forEach(row => {
        const text = row.innerText.toLowerCase();
        if (text.includes(query)) {
            row.style.display = '';
            visibleCount++;
        } else {
            row.style.display = 'none';
        }
    });
    const recordsCount = document.getElementById('report-records-count');
    if (recordsCount) {
        recordsCount.innerText = `${visibleCount} de ${rows.length} registro(s)`;
    }
}

function exportCurrentReportPDF() {
    const params = getReportFilterParams();
    showToast("Generando reporte PDF...", "success");
    window.location.href = `/api/reports/export/pdf?${params.toString()}`;
}

function exportCurrentReportExcel() {
    const params = getReportFilterParams();
    showToast("Generando reporte Excel (.xlsx)...", "success");
    window.location.href = `/api/reports/export/excel?${params.toString()}`;
}

