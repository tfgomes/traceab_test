// Global state
let currentData = [];
let currentColumns = [];
let currentOffset = 0;
let currentLimit = 10;
let totalRecords = 0;
let filterReloadTimeout = null;

// All filterable columns - must match FILTER_COLUMNS in databricks_client.py
const FILTER_COLUMNS = [
    'schema_name',
    'country',
    'product_division',
    'region',
    'factory',
    'business_divison'
];

// API Base URL
const API_BASE = window.location.origin;

// Initialize on page load
window.addEventListener('DOMContentLoaded', async () => {
    await loadFilterValues();
    await loadRecords();

    // Add input listeners for cascading filters
    FILTER_COLUMNS.forEach(col => {
        const input = document.getElementById(`filter_${col}`);
        if (input) {
            input.addEventListener('input', () => {
                clearTimeout(filterReloadTimeout);
                filterReloadTimeout = setTimeout(async () => {
                    await loadFilterValues();
                }, 500);
            });

            input.addEventListener('blur', async () => {
                clearTimeout(filterReloadTimeout);
                await loadFilterValues();
            });

            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') applyFilters();
            });
        }
    });
});

// Load filter dropdown values with cascading support
async function loadFilterValues() {
    try {
        const filters = getFilters();
        const queryParams = new URLSearchParams(filters);
        const response = await fetch(`${API_BASE}/api/filter-values?${queryParams.toString()}`);

        if (!response.ok) throw new Error(`Failed to load filter values: ${response.status}`);

        const filterValues = await response.json();

        FILTER_COLUMNS.forEach(col => {
            const datalist = document.getElementById(`values_${col}`);
            if (datalist && filterValues[col]) {
                const parent = datalist.parentNode;
                const newDatalist = document.createElement('datalist');
                newDatalist.id = datalist.id;
                filterValues[col].forEach(value => {
                    const option = document.createElement('option');
                    option.value = value;
                    newDatalist.appendChild(option);
                });
                parent.replaceChild(newDatalist, datalist);
            }
        });
    } catch (error) {
        console.error('Error loading filter values:', error);
        showMessage('Warning: Could not load filter suggestions. You can still type values manually.', 'warning');
    }
}

// Utility functions
function showLoading() {
    document.getElementById('loadingIndicator').style.display = 'block';
}

function hideLoading() {
    document.getElementById('loadingIndicator').style.display = 'none';
}

function showMessage(message, type = 'success') {
    const messageBox = document.getElementById('messageBox');
    messageBox.textContent = message;
    messageBox.className = `message-box ${type}`;
    messageBox.style.display = 'block';
    setTimeout(() => { messageBox.style.display = 'none'; }, 5000);
}

// Get current filter values from inputs
function getFilters() {
    const filters = {};
    FILTER_COLUMNS.forEach(col => {
        const input = document.getElementById(`filter_${col}`);
        if (input && input.value.trim()) {
            filters[col] = input.value.trim();
        }
    });
    return filters;
}

// Build query string
function buildQueryString(offset = 0) {
    const params = new URLSearchParams();
    params.append('limit', currentLimit);
    params.append('offset', offset);
    const filters = getFilters();
    for (const [key, value] of Object.entries(filters)) {
        params.append(key, value);
    }
    return params.toString();
}

// Load records from API
async function loadRecords(offset = 0) {
    showLoading();
    currentOffset = offset;

    try {
        const url = `${API_BASE}/api/records?${buildQueryString(offset)}`;
        const response = await fetch(url);

        if (!response.ok) {
            const errorText = await response.text();
            let error;
            try { error = JSON.parse(errorText); } catch { error = { detail: errorText }; }
            throw new Error(error.detail || `Failed to load records: ${response.status}`);
        }

        const data = await response.json();
        currentData = data.records;
        currentColumns = data.columns;
        totalRecords = data.total_count;

        displayRecords();
        updatePagination();

    } catch (error) {
        console.error('Error loading records:', error);
        showMessage(error.message || 'Failed to load records', 'error');
    } finally {
        hideLoading();
    }
}

// Render table
function displayRecords() {
    const thead = document.getElementById('tableHead');
    const tbody = document.getElementById('tableBody');
    const recordCount = document.getElementById('recordCount');

    thead.innerHTML = '';
    tbody.innerHTML = '';

    if (currentData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="100" style="text-align:center; padding: 40px; color: #666;">No records found</td></tr>';
        recordCount.textContent = 'No records';
        return;
    }

    // Build header
    const headerRow = document.createElement('tr');
    currentColumns.forEach(col => {
        const th = document.createElement('th');
        th.textContent = col;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);

    // Build rows
    currentData.forEach(record => {
        const row = document.createElement('tr');
        currentColumns.forEach(col => {
            const td = document.createElement('td');
            const value = record[col];
            td.textContent = value !== null && value !== undefined ? value : '';
            td.title = td.textContent;
            row.appendChild(td);
        });
        tbody.appendChild(row);
    });

    // Update record count label
    const start = currentOffset + 1;
    const end = Math.min(currentOffset + currentData.length, totalRecords);
    recordCount.textContent = `Showing ${start.toLocaleString()} – ${end.toLocaleString()} of ${totalRecords.toLocaleString()} records`;
}

// Pagination controls
function updatePagination() {
    const pageInfo = document.getElementById('pageInfo');
    const prevBtn = document.getElementById('prevBtn');
    const nextBtn = document.getElementById('nextBtn');

    const currentPage = Math.floor(currentOffset / currentLimit) + 1;
    const totalPages = Math.max(1, Math.ceil(totalRecords / currentLimit));

    pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    prevBtn.disabled = currentOffset === 0;
    nextBtn.disabled = currentOffset + currentLimit >= totalRecords;
}

function previousPage() {
    if (currentOffset > 0) loadRecords(Math.max(0, currentOffset - currentLimit));
}

function nextPage() {
    if (currentOffset + currentLimit < totalRecords) loadRecords(currentOffset + currentLimit);
}

// Filter actions
async function applyFilters() {
    currentOffset = 0;
    await loadFilterValues();
    await loadRecords(0);
}

async function clearFilters() {
    FILTER_COLUMNS.forEach(col => {
        const input = document.getElementById(`filter_${col}`);
        if (input) input.value = '';
    });
    await applyFilters();
}