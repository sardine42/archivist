/* Keyword search for the paper list. Indexes are loaded only after typing. */
'use strict';

const searchInput = document.getElementById('paperSearch');
const searchStatus = document.getElementById('paperSearchStatus');
const searchRetry = document.getElementById('paperSearchRetry');
const paperContainer = document.getElementById('paperCards');
const paperCards = [...paperContainer.querySelectorAll('.paper-card')];
const pageSize = 6;
const previousPage = document.getElementById('paperPrev');
const nextPage = document.getElementById('paperNext');
const pagination = document.getElementById('paperPagination');
const pageStatus = document.getElementById('paperPageStatus');
const resultCount = document.getElementById('paperResultCount');
const cardsBySlug = new Map(paperCards.map(card => [card.dataset.slug, card]));
const headingsBySlug = new Map(paperCards.map(card => [card.dataset.slug, [
    normalizeSearchText(card.querySelector('.card-title').textContent),
    normalizeSearchText(card.dataset.modelName || ''),
]]));

let searchMatches = null;
let searchTerms = [];
let searchVersion = 0;
let searchTimer;
let searchLoading = null;
let searchPending = false;
let composing = false;
let currentPage = 1;
let orderedCards = [...paperCards];

function normalizeSearchText(value) {
    return value.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim();
}

function keywordTerms(value) {
    // Retain model punctuation (e.g. DCN-V2), while treating quotes as separators.
    return normalizeSearchText(value).replace(/["“”]/g, ' ').split(/\s+/).filter(Boolean);
}

function headingMatches(slug) {
    return searchTerms.some(term => headingsBySlug.get(slug).some(text => text.includes(term)));
}

function directHeadingMatches() {
    const matches = new Map();
    headingsBySlug.forEach((headings, slug) => {
        if (searchTerms.every(term => headings.some(text => text.includes(term)))) {
            matches.set(slug, {});
        }
    });
    return matches;
}

function setDropdownOpen(dropdown, open) {
    dropdown.classList.toggle('open', open);
    dropdown.querySelector('.dropdown-btn').setAttribute('aria-expanded', String(open));
}

function toggleDropdown(id) {
    const selected = document.getElementById(id);
    const shouldOpen = !selected.classList.contains('open');
    document.querySelectorAll('.dropdown-multi').forEach(dropdown => {
        setDropdownOpen(dropdown, dropdown === selected && shouldOpen);
    });
}

document.addEventListener('click', event => {
    if (!event.target.closest('.dropdown-multi')) {
        document.querySelectorAll('.dropdown-multi').forEach(dropdown => setDropdownOpen(dropdown, false));
    }
});

document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.dropdown-multi.open').forEach(dropdown => {
        setDropdownOpen(dropdown, false);
        dropdown.querySelector('.dropdown-btn').focus();
    });
});

function getChecked(id) {
    return [...document.querySelectorAll(`#${id} input:checked`)].map(input => input.value);
}

function getTotal(id) {
    return document.querySelectorAll(`#${id} input[type="checkbox"]`).length;
}

function checkAll(id) {
    document.querySelectorAll(`#${id} input`).forEach(input => { input.checked = true; });
    confirmFilters();
}

function clearAll(id) {
    document.querySelectorAll(`#${id} input`).forEach(input => { input.checked = false; });
    confirmFilters();
}

function updateLabel(id) {
    const inputs = [...document.querySelectorAll(`#${id} input[type="checkbox"]`)];
    const checked = inputs.filter(input => input.checked);
    let label = `${checked.length}/${inputs.length}`;
    if (checked.length === inputs.length) label = '全部';
    else if (!checked.length) label = '无';
    else if (checked.length === 1) label = checked[0].dataset.label || checked[0].value;
    const button = document.querySelector(`#${id} .dropdown-btn`);
    if (id === 'categoryFilter' && checked.length === 1 && checked.length !== inputs.length) {
        const badge = checked[0].parentElement.querySelector('.category-badge').cloneNode(true);
        button.replaceChildren(badge, document.createTextNode(' ▾'));
    } else {
        button.textContent = label + ' ▾';
    }
}

const snippetObserver = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            snippetObserver.unobserve(entry.target);
            loadSnippet(entry.target);
        }
    });
}, { rootMargin: '150px' }) : null;

function applyFilters() {
    const cats = getChecked('categoryFilter');
    const companies = getChecked('companyFilter');
    const tags = getChecked('tagFilter');
    const allCats = cats.length === getTotal('categoryFilter');
    const allCompanies = companies.length === getTotal('companyFilter');
    const allTags = tags.length === getTotal('tagFilter');
    ['categoryFilter', 'companyFilter', 'tagFilter'].forEach(updateLabel);
    snippetObserver?.disconnect();

    const matchingCards = orderedCards.filter(card => {
        const slug = card.dataset.slug;
        const categoryOK = allCats || card.dataset.category.split(',').some(cat => cats.includes(cat));
        const companyOK = allCompanies || companies.includes(card.dataset.company);
        const tagOK = allTags || card.dataset.tags.split(',').some(tag => tags.includes(tag));
        const match = searchMatches?.get(slug);
        return categoryOK && companyOK && tagOK && (searchMatches === null || !!match);
    });
    const totalPages = Math.max(1, Math.ceil(matchingCards.length / pageSize));
    currentPage = Math.min(Math.max(1, currentPage), totalPages);
    const visibleCards = new Set(matchingCards.slice((currentPage - 1) * pageSize, currentPage * pageSize));

    paperCards.forEach(card => {
        const slug = card.dataset.slug;
        const match = searchMatches?.get(slug);
        const show = visibleCards.has(card);
        card.style.display = show ? '' : 'none';

        const label = card.querySelector('.search-match-label');
        label.hidden = !match;
        if (match) {
            const isHeading = headingMatches(slug);
            card.dataset.searchMatch = isHeading ? 'heading' : 'content';
            label.textContent = isHeading ? '标题 / 模型名匹配' : '内容匹配';
        } else {
            delete card.dataset.searchMatch;
        }
        const snippet = card.querySelector('.card-search-snippet');
        if (!match?.result) {
            snippet.hidden = true;
        } else if (show) {
            if (snippetObserver) snippetObserver.observe(card);
            else loadSnippet(card);
        }
    });

    const hasFilters = searchMatches !== null || !allCats || !allCompanies || !allTags;
    resultCount.textContent = hasFilters ? `${matchingCards.length} 篇匹配论文` : '全部论文';
    pagination.hidden = matchingCards.length <= pageSize;
    pageStatus.textContent = matchingCards.length ? `${currentPage} / ${totalPages}` : '';
    previousPage.disabled = currentPage === 1;
    nextPage.disabled = currentPage === totalPages;
    document.getElementById('paperSearchEmpty').hidden = matchingCards.length !== 0 || searchPending;
}

function compareSelectedSort(a, b) {
    const sortBy = document.getElementById('sortSelect').value;
    const field = {
        published_date: 'publishedDate', date_added: 'dateAdded',
        reading_score: 'readingScore', score: 'score', rating: 'rating',
    }[sortBy];
    if (sortBy === 'published_date' || sortBy === 'date_added') {
        return (b.dataset[field] || '').localeCompare(a.dataset[field] || '');
    }
    return (parseFloat(b.dataset[field]) || 0) - (parseFloat(a.dataset[field]) || 0);
}

function sortCards() {
    orderedCards = [...paperCards].sort((a, b) => {
        if (searchMatches !== null) {
            // A hard tier, rather than a weight: body frequency cannot overtake a heading hit.
            const headingOrder = Number(headingMatches(b.dataset.slug)) - Number(headingMatches(a.dataset.slug));
            if (headingOrder) return headingOrder;
        }
        return compareSelectedSort(a, b);
    });
    const fragment = document.createDocumentFragment();
    orderedCards.forEach(card => fragment.appendChild(card));
    paperContainer.appendChild(fragment);
}

function confirmFilters({ resetPage = true } = {}) {
    if (resetPage) currentPage = 1;
    sortCards();
    applyFilters();
}

function withTimeout(promise, milliseconds = 20000) {
    let timer;
    return Promise.race([
        promise,
        new Promise((resolve, reject) => {
            timer = setTimeout(() => reject(new Error('Search timed out')), milliseconds);
        }),
    ]).finally(() => clearTimeout(timer));
}

async function loadSearch() {
    if (!searchLoading) {
        searchLoading = withTimeout(Promise.all([
            import('/search/pagefind/pagefind.js'),
            fetch(`/search/pagefind/papers.json?ts=${Date.now()}`, { cache: 'no-store' }).then(response => {
                if (!response.ok) throw new Error('Search index unavailable');
                return response.json();
            }),
        ])).then(async ([pagefind, slugs]) => {
            await pagefind.options({ baseUrl: '/' });
            return { pagefind, slugs };
        }).catch(error => {
            searchLoading = null;
            throw error;
        });
    }
    return searchLoading;
}

function clearSnippets() {
    snippetObserver?.disconnect();
    paperCards.forEach(card => {
        const snippet = card.querySelector('.card-search-snippet');
        snippet.hidden = true;
        snippet.replaceChildren();
    });
}

async function runKeywordSearch(version) {
    const query = searchInput.value.trim();
    try {
        const { pagefind, slugs } = await loadSearch();
        if (version !== searchVersion) return;
        const response = await withTimeout(pagefind.search(query));
        if (version !== searchVersion) return;
        const matches = directHeadingMatches();
        response.results.forEach(result => {
            const slug = slugs[result.id];
            if (!slug) throw new Error('Search index is incomplete');
            if (cardsBySlug.has(slug)) matches.set(slug, { result });
        });
        searchMatches = matches;
        searchPending = false;
        searchStatus.textContent = '';
        searchRetry.hidden = true;
        confirmFilters({ resetPage: false });
    } catch (error) {
        if (version !== searchVersion) return;
        searchLoading = null;
        searchPending = false;
        searchStatus.textContent = '正文搜索暂时不可用，当前仅显示标题和模型名匹配。';
        searchRetry.hidden = false;
        console.warn('Paper search failed:', error);
        confirmFilters({ resetPage: false });
    }
}

function scheduleKeywordSearch(immediate = false) {
    clearTimeout(searchTimer);
    const version = ++searchVersion;
    searchTerms = keywordTerms(searchInput.value);
    clearSnippets();
    searchRetry.hidden = true;
    if (!searchTerms.length) {
        searchPending = false;
        searchMatches = null;
        searchStatus.textContent = '';
        confirmFilters();
        return;
    }
    searchPending = true;
    searchMatches = directHeadingMatches();
    searchStatus.textContent = '正在搜索标题和正文…';
    confirmFilters();
    searchTimer = setTimeout(() => runKeywordSearch(version), immediate ? 0 : 220);
}

async function loadSnippet(card) {
    const version = searchVersion;
    const match = searchMatches?.get(card.dataset.slug);
    if (!match?.result) return;
    const snippet = card.querySelector('.card-search-snippet');
    try {
        match.data ||= withTimeout(match.result.data());
        const data = await match.data;
        if (version !== searchVersion) return;
        // Keep only text and <mark>, even if an index contains unexpected markup.
        const template = document.createElement('template');
        template.innerHTML = data.excerpt;
        const fragment = document.createDocumentFragment();
        template.content.childNodes.forEach(node => {
            if (node.nodeName === 'MARK') {
                const mark = document.createElement('mark');
                mark.textContent = node.textContent;
                fragment.appendChild(mark);
            } else {
                fragment.appendChild(document.createTextNode(node.textContent));
            }
        });
        snippet.replaceChildren(fragment);
        snippet.hidden = !snippet.textContent;
    } catch (error) {
        if (version !== searchVersion) return;
        match.data = null;
        snippet.textContent = '命中片段暂时无法加载，可点击标题查看论文。';
        snippet.hidden = false;
    }
}

searchInput.addEventListener('compositionstart', () => {
    composing = true;
    clearTimeout(searchTimer);
    ++searchVersion;
});
searchInput.addEventListener('compositionend', () => {
    composing = false;
    scheduleKeywordSearch();
});
searchInput.addEventListener('input', event => {
    if (!composing && !event.isComposing) scheduleKeywordSearch();
});
searchInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !composing && !event.isComposing) {
        event.preventDefault();
        scheduleKeywordSearch(true);
    }
});
searchRetry.addEventListener('click', () => scheduleKeywordSearch(true));
['categoryFilter', 'companyFilter', 'tagFilter'].forEach(id => {
    const dropdown = document.getElementById(id);
    setDropdownOpen(dropdown, false);
    dropdown.addEventListener('change', () => confirmFilters());
});
document.getElementById('sortSelect').addEventListener('change', () => confirmFilters());
// Private score values arrive only after a fresh server session check, and are
// removed again on logout, tab changes, and back/forward cache transitions.
document.addEventListener('archivist:auth-change', () => confirmFilters({ resetPage: false }));
previousPage.addEventListener('click', () => {
    if (previousPage.disabled) return;
    currentPage--;
    applyFilters();
});
nextPage.addEventListener('click', () => {
    if (nextPage.disabled) return;
    currentPage++;
    applyFilters();
});
// Restore a browser-preserved search value when returning from a paper detail.
if (searchInput.value.trim()) scheduleKeywordSearch(true);
else confirmFilters();
