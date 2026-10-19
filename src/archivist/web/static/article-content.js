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

    function prepareInlineMath() {
        document.querySelectorAll('.article-content .katex:not(.katex-display .katex)').forEach(function (formula) {
            formula.classList.remove('content-math-scroll');
            formula.removeAttribute('tabindex');
            const container = formula.closest('p, li, blockquote, td, th, figcaption, .article-content');
            const style = getComputedStyle(container);
            const availableWidth = container.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
            if (formula.getBoundingClientRect().width > availableWidth + 1) {
                formula.classList.add('content-math-scroll');
                formula.tabIndex = 0;
            }
        });
    }

    function prepareContent() {
        prepareTables();
        prepareInlineMath();
        if (document.fonts) document.fonts.ready.then(prepareInlineMath);
        let resizeFrame;
        window.addEventListener('resize', function () {
            cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(prepareInlineMath);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', prepareContent, { once: true });
    } else {
        prepareContent();
    }
}());
