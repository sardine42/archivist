(function () {
    'use strict';

    function prepareTables() {
        document.querySelectorAll('.article-content table').forEach(function (table) {
            let wrapper = table.parentElement;
            if (!wrapper.classList.contains('content-table-scroll')) {
                wrapper = document.createElement('div');
                wrapper.className = 'content-table-scroll';
                table.replaceWith(wrapper);
                wrapper.append(table);
            }
            wrapper.tabIndex = 0;
            wrapper.setAttribute('role', 'region');
            wrapper.setAttribute('aria-label', '数据表格，可横向滚动');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', prepareTables, { once: true });
    } else {
        prepareTables();
    }
}());
