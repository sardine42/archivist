(function () {
    'use strict';
    const root = document.querySelector('.reading-detail');
    if (!root) return;
    const article = root.querySelector('.rd-article');

    // Standalone Markdown images become figures. The image and its full
    // description remain available; expanding changes only the figure width.
    article.querySelectorAll('p').forEach(function (paragraph) {
        const image = paragraph.querySelector(':scope > img:only-child');
        if (!image || paragraph.textContent.trim()) return;
        const figure = document.createElement('figure');
        figure.className = 'rd-figure';
        const controls = document.createElement('div');
        controls.className = 'rd-figure-head';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'rd-figure-button';
        button.textContent = '加宽图表';
        button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', function () {
            const expanded = figure.classList.toggle('rd-expanded');
            button.setAttribute('aria-pressed', String(expanded));
            button.textContent = expanded ? '恢复图宽' : '加宽图表';
        });
        controls.append(button);
        figure.append(controls, image);
        if (image.alt.trim()) {
            const caption = document.createElement('figcaption');
            caption.textContent = image.alt;
            figure.append(caption);
            if (typeof window.renderMathInElement === 'function') {
                window.renderMathInElement(caption, {
                    delimiters: [
                        { left: '$$', right: '$$', display: true },
                        { left: '$', right: '$', display: false }
                    ],
                    throwOnError: false
                });
            }
        }
        paragraph.replaceWith(figure);
    });

    const tocLinks = Array.from(root.querySelectorAll('.rd-toc-links a[href^="#"]'));
    const desktopLinks = Array.from(root.querySelectorAll('.rd-contents a[href^="#"]'));
    const sections = desktopLinks.map(function (link) {
        let id;
        try { id = decodeURIComponent(link.getAttribute('href').slice(1)); }
        catch (_) { return null; }
        const heading = document.getElementById(id);
        return heading ? { heading: heading, href: link.getAttribute('href') } : null;
    }).filter(Boolean);
    let activeHref = null;
    let scheduled = false;

    function updateContents() {
        scheduled = false;
        if (!sections.length) return;
        let current = sections[0];
        for (let i = sections.length - 1; i >= 0; i--) {
            if (sections[i].heading.getBoundingClientRect().top <= 72) {
                current = sections[i];
                break;
            }
        }
        if (current.href === activeHref) return;
        activeHref = current.href;
        tocLinks.forEach(function (link) {
            if (link.getAttribute('href') === activeHref) link.setAttribute('aria-current', 'location');
            else link.removeAttribute('aria-current');
        });
        const sidebar = root.querySelector('.rd-contents');
        const active = desktopLinks.find(function (link) { return link.getAttribute('href') === activeHref; });
        if (sidebar && active && sidebar.clientHeight) {
            const position = active.getBoundingClientRect();
            const bounds = sidebar.getBoundingClientRect();
            if (position.top < bounds.top || position.bottom > bounds.bottom) {
                sidebar.scrollTop += position.top - bounds.top - sidebar.clientHeight / 3;
            }
        }
    }

    window.addEventListener('scroll', function () {
        if (!scheduled) {
            scheduled = true;
            window.requestAnimationFrame(updateContents);
        }
    }, { passive: true });
    root.querySelectorAll('.rd-mobile-toc a').forEach(function (link) {
        link.addEventListener('click', function () {
            root.querySelector('.rd-mobile-toc').open = false;
        });
    });
    updateContents();
}());
