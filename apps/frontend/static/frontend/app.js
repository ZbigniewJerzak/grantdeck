document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-copy-target]');
  if (!button) return;

  const source = document.getElementById(button.dataset.copyTarget);
  if (!source) return;

  navigator.clipboard.writeText(source.textContent.trim()).then(() => {
    const originalLabel = button.getAttribute('aria-label');
    button.setAttribute('aria-label', 'Token copied');
    button.textContent = '✓';
    window.setTimeout(() => {
      button.setAttribute('aria-label', originalLabel || 'Copy token');
      button.textContent = '⧉';
    }, 1600);
  });
});

document.querySelectorAll('.js-data-table').forEach((table) => {
  if (typeof window.DataTable === 'undefined') return;

  new window.DataTable(table, {
    pageLength: Number(table.dataset.pageLength || 25),
    lengthMenu: [10, 25, 50, 100],
    order: [[
      Number(table.dataset.orderColumn || 0),
      table.dataset.orderDirection || 'asc',
    ]],
    columnDefs: [
      { targets: [4, 5, 6], type: 'num' },
      { targets: 7, type: 'date' },
    ],
    language: {
      search: 'Filter projects:',
      emptyTable: 'No projects are available for quota status.',
    },
  });
});
