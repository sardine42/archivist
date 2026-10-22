(function () {
    'use strict';
    const root = document.getElementById('graphExplorer');
    if (!root) return;
    const byId = function (id) { return document.getElementById(id); };
    const searchInput = byId('graphSearch'), results = byId('graphSearchResults');
    const topicPicker = byId('graphTopic'), inspector = byId('graphInspector');
    const companyPicker = byId('graphCompany'), companyNames = new Map();
    const panel = byId('graphPanelBody'), loading = byId('graphLoading');
    const categoryNames = {'generative-rec': '生成式', 'discriminative-rec': '判别式', llm: 'LLM', other: '其他'};
    let network, map, nodes, links, relations, linksByNode;
    let selected = null, selectedLink = null, activeTopic = '', activeCompany = '', detailMode = null;
    let searchMatches = [], searchIndex = -1;

    function el(tag, className, value) {
        const item = document.createElement(tag);
        if (className) item.className = className;
        if (value != null) item.textContent = value;
        return item;
    }
    function button(label, className, callback) {
        const item = el('button', className, label);
        item.type = 'button';
        item.addEventListener('click', callback);
        return item;
    }
    function label(node) { return node.model_name || node.title || node.id; }
    function date(node) { return node.published_date || '日期未知'; }
    function companyKey(node) {
        const company = String(node.company || '').trim();
        return company ? 'company:' + company : 'unattributed';
    }
    function matchesCompany(node) { return !activeCompany || companyKey(node) === activeCompany; }
    function matchesScope(node) { return matchesCompany(node) && (!activeTopic || node.tags.includes(activeTopic)); }
    function filteredNodes() {
        const query = searchInput.value.trim();
        return (query ? search(query) : network.nodes).filter(matchesScope);
    }
    function newest(items) {
        return items.slice().sort(function (a, b) {
            return (b.published_date || '').localeCompare(a.published_date || '') || label(a).localeCompare(label(b));
        });
    }
    function section(title) {
        const part = el('section', 'gx-section');
        part.append(el('h3', '', title));
        return part;
    }
    function badges(node) {
        const wrap = el('span', 'category-badges');
        (node.category || []).forEach(function (type) {
            wrap.append(el('span', 'category-badge category-' + (categoryNames[type] ? type : 'other'), categoryNames[type] || type));
        });
        return wrap;
    }
    function tags(values) {
        const wrap = el('div', 'gx-tags');
        values.forEach(function (tag) {
            const item = button(tag, 'gx-tag', function () { chooseTopic(activeTopic === tag ? '' : tag); });
            item.setAttribute('aria-pressed', String(activeTopic === tag));
            wrap.append(item);
        });
        return wrap;
    }
    function nodeRow(node, description) {
        const row = button('', 'gx-node-row', function () { select(node.id, true); });
        const top = el('span', 'gx-row-top');
        top.append(el('span', 'gx-row-title', label(node)), el('time', 'gx-row-date', date(node)));
        row.append(top);
        if (node.title !== label(node)) row.append(el('span', 'gx-row-description', node.title));
        if (description) row.append(el('span', 'gx-row-relation', description));
        return row;
    }
    function setURL(id) {
        const url = new URL(location.href);
        if (id) url.searchParams.set('focus', id);
        else url.searchParams.delete('focus');
        history.replaceState(null, '', url);
    }
    function normalize(value) { return String(value || '').normalize('NFKC').toLocaleLowerCase(); }
    function search(query) {
        const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
        if (!terms.length) return [];
        return network.nodes.map(function (node) {
            const names = [label(node)].concat(node.aliases || []).map(normalize);
            const title = normalize(node.title), topics = (node.tags || []).map(normalize);
            const summary = normalize(node.summary);
            let score = 0;
            for (const term of terms) {
                if (names.some(function (name) { return name === term; })) score += 120;
                else if (names.some(function (name) { return name.startsWith(term); })) score += 80;
                else if (names.some(function (name) { return name.includes(term); })) score += 60;
                else if (title.includes(term)) score += 40;
                else if (topics.some(function (tag) { return tag.includes(term); })) score += 15;
                else if (summary.includes(term)) score += 5;
                else return null;
            }
            return {node: node, score: score};
        }).filter(Boolean).sort(function (a, b) {
            return b.score - a.score || (b.node.published_date || '').localeCompare(a.node.published_date || '') || label(a.node).localeCompare(label(b.node));
        }).map(function (item) { return item.node; });
    }
    function updateHighlight() {
        const query = searchInput.value.trim();
        const matching = query ? new Set(search(query).map(function (node) { return node.id; })) : null;
        const members = network.nodes.filter(function (node) {
            return matchesScope(node) && (!matching || matching.has(node.id));
        });
        const ids = activeTopic || query ? members.map(function (node) { return node.id; }) : null;
        map.setHighlight(ids);
        byId('graphCount').textContent = activeCompany || activeTopic || query ? '已筛选 ' + members.length + ' / ' + nodes.size + ' 篇论文' : '已收录 ' + nodes.size + ' 篇论文';
    }
    function relationText(relation, focusId) {
        if (relation.type === 'citation') return relation.source === focusId ? '本篇引用' : '引用本篇';
        return '实验比较';
    }
    function evidenceItems(pair) {
        return pair.relation_ids.map(function (id) { return relations.get(id); }).filter(Boolean);
    }
    function appendEvidence(parent, items) {
        items.forEach(function (relation) {
            const from = nodes.get(relation.source), to = nodes.get(relation.target);
            if (!from || !to) return;
            const item = el('details', 'gx-evidence');
            item.append(el('summary', '', label(from) + (relation.type === 'citation' ? ' 引用 ' : ' → ') + label(to) + (relation.type === 'comparison' ? ' · 实验比较' : '')));
            if (relation.summary) item.append(el('p', '', relation.summary));
            const datasets = Object.keys(relation.datasets || {});
            if (datasets.length) item.append(el('p', '', '数据集：' + datasets.join('、')));
            if (relation.is_self_reported) item.append(el('p', '', '由论文作者报告'));
            const source = nodes.get('paper:' + (relation.paper_id || '').replace(/v\d+$/, ''));
            if (source && source.reading_url) {
                const link = el('a', '', '来源：' + label(source) + ' ↗');
                link.href = source.reading_url;
                item.append(link);
            }
            parent.append(item);
        });
    }
    function renderNode(node, expanded) {
        const main = el('div', 'gx-paper-main');
        const title = el('h2', 'gx-panel-title');
        // This HTML comes from the shared server-side title formatter, which escapes metadata.
        if (node.title_html) title.innerHTML = node.title_html;
        else title.textContent = node.title;
        main.append(title);
        const meta = el('div', 'gx-panel-meta');
        meta.append(badges(node), el('time', '', date(node)));
        if (node.company) meta.append(el('span', '', node.company));
        main.append(meta);
        if (node.summary) main.append(el('p', 'gx-summary', node.summary));
        if (node.tags.length) main.append(tags(node.tags));
        const actions = el('div', 'gx-panel-links');
        if (node.reading_url) {
            const link = el('a', '', '阅读全文 ↗');
            link.href = node.reading_url;
            actions.append(link);
        }
        if (node.arxiv_url) {
            const link = el('a', '', 'arXiv ↗');
            link.href = node.arxiv_url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            actions.append(link);
        }
        main.append(actions);
        panel.append(main);
        const neighbors = (linksByNode.get(node.id) || []).filter(function (pair) {
            return matchesCompany(nodes.get(pair.source === node.id ? pair.target : pair.source));
        }).sort(function (a, b) {
            return Number(!!b.relation_ids.length) - Number(!!a.relation_ids.length) || b.weight - a.weight || a.id.localeCompare(b.id);
        });
        if (neighbors.length) {
            const related = section('相关论文 · ' + neighbors.length);
            const list = el('div', 'gx-related-list');
            neighbors.slice(0, expanded ? neighbors.length : 6).forEach(function (pair) {
                const other = nodes.get(pair.source === node.id ? pair.target : pair.source);
                const recorded = Array.from(new Set(evidenceItems(pair).map(function (relation) { return relationText(relation, node.id); })));
                const common = pair.shared_tags.filter(function (tag) { return tag !== 'industrial' && tag !== 'academic'; });
                const row = el('div', 'gx-related-item');
                row.append(nodeRow(other, recorded.length ? recorded.join(' · ') : common.slice(0, 3).join(' · ')));
                row.append(button('关联依据', 'gx-evidence-button', function () { selectLink(pair.id); }));
                list.append(row);
            });
            related.append(list);
            if (!expanded && neighbors.length > 6) related.append(button('展开全部 ' + neighbors.length + ' 篇', 'gx-more', function () {
                panel.replaceChildren();
                renderNode(node, true);
            }));
            panel.append(related);
        }
    }
    function renderFiltered() {
        const members = newest(filteredNodes());
        const names = [companyNames.get(activeCompany), activeTopic, searchInput.value.trim()].filter(Boolean);
        const part = section(names.concat(members.length + ' 篇论文').join(' · '));
        const list = el('div', 'gx-topic-members');
        members.forEach(function (node) { list.append(nodeRow(node)); });
        if (!members.length) list.append(el('p', 'gx-search-empty', '没有符合条件的论文。'));
        part.append(list);
        panel.append(part);
    }
    function renderLink(pair) {
        const main = el('div', 'gx-paper-main');
        const heading = el('div', 'gx-pair-heading');
        heading.append(button(label(nodes.get(pair.source)), 'gx-pair-node', function () { select(pair.source, true); }), el('span', '', '—'), button(label(nodes.get(pair.target)), 'gx-pair-node', function () { select(pair.target, true); }));
        main.append(heading);
        if (pair.shared_tags.length) {
            main.append(el('h3', 'gx-mini-heading', '共同标签'), tags(pair.shared_tags));
        }
        panel.append(main);
        const evidence = evidenceItems(pair);
        if (evidence.length) {
            const part = section('记录的引用与实验比较 · ' + evidence.length);
            appendEvidence(part, evidence);
            panel.append(part);
        }
    }
    function renderDetail() {
        panel.replaceChildren();
        inspector.hidden = !detailMode;
        if (!detailMode) return;
        byId('graphPanelLabel').textContent = detailMode === 'node' ? '论文与相关研究' : detailMode === 'link' ? '关联依据' : '筛选结果';
        if (detailMode === 'node') renderNode(nodes.get(selected), false);
        else if (detailMode === 'link') renderLink(links.get(selectedLink));
        else renderFiltered();
    }
    function select(id, focus, updateURL) {
        const node = nodes.get(id);
        if (!node || !matchesCompany(node)) return;
        selected = id;
        selectedLink = null;
        detailMode = 'node';
        hideSearch();
        map.setSelection(id);
        if (focus) map.focus(id);
        renderDetail();
        if (updateURL !== false) setURL(id);
    }
    function selectLink(id) {
        if (!links.has(id)) return;
        selected = null;
        selectedLink = id;
        detailMode = 'link';
        map.setSelection(null, {linkId: id});
        hideSearch();
        setURL(null);
        renderDetail();
    }
    function clearSelection() {
        selected = null;
        selectedLink = null;
        detailMode = null;
        map.setSelection(null);
        if (document.activeElement && byId('graphCanvas').contains(document.activeElement)) document.activeElement.blur();
        setURL(null);
        hideSearch();
        renderDetail();
    }
    function chooseTopic(value) {
        activeTopic = value;
        topicPicker.value = value;
        if (!selected && !selectedLink) detailMode = activeTopic || activeCompany ? 'filter' : null;
        updateHighlight();
        renderDetail();
    }
    function chooseCompany(value) {
        activeCompany = value;
        if (selected && !matchesCompany(nodes.get(selected)) || selectedLink && [links.get(selectedLink).source, links.get(selectedLink).target].some(function (id) { return !matchesCompany(nodes.get(id)); })) clearSelection();
        map.setFilter(activeCompany ? network.nodes.filter(matchesCompany).map(function (node) { return node.id; }) : null);
        if (!selected && !selectedLink) detailMode = activeCompany || activeTopic ? 'filter' : null;
        updateHighlight();
        renderDetail();
        hideSearch();
        map.overview();
    }
    function hideSearch() {
        results.hidden = true;
        searchInput.setAttribute('aria-expanded', 'false');
        searchInput.removeAttribute('aria-activedescendant');
        searchIndex = -1;
    }
    function showSearch() {
        if (!network) return;
        updateHighlight();
        if (detailMode === 'filter') renderDetail();
        results.replaceChildren();
        if (!searchInput.value.trim()) { hideSearch(); return; }
        searchMatches = search(searchInput.value).filter(matchesScope).slice(0, 8);
        searchIndex = -1;
        searchInput.removeAttribute('aria-activedescendant');
        searchMatches.forEach(function (node, index) {
            const option = button('', 'gx-search-option', function () { select(node.id, true); });
            option.id = 'gx-search-option-' + index;
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', 'false');
            const top = el('span', 'gx-row-top');
            top.append(el('span', 'gx-row-title', label(node)), el('time', 'gx-row-date', date(node)));
            option.append(top, el('span', 'gx-row-description', node.title));
            results.append(option);
        });
        if (!searchMatches.length) results.append(el('p', 'gx-search-empty', '没有找到相关论文。'));
        results.hidden = false;
        searchInput.setAttribute('aria-expanded', 'true');
    }
    function wire() {
        searchInput.addEventListener('input', showSearch);
        searchInput.addEventListener('focus', showSearch);
        searchInput.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !results.hidden) { hideSearch(); event.stopPropagation(); return; }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                if (results.hidden) showSearch();
                if (!searchMatches.length) return;
                event.preventDefault();
                searchIndex = searchIndex < 0 ? (event.key === 'ArrowDown' ? 0 : searchMatches.length - 1) : (searchIndex + (event.key === 'ArrowDown' ? 1 : -1) + searchMatches.length) % searchMatches.length;
                results.querySelectorAll('[role="option"]').forEach(function (option, index) {
                    option.setAttribute('aria-selected', String(index === searchIndex));
                    if (index === searchIndex) {
                        searchInput.setAttribute('aria-activedescendant', option.id);
                        option.scrollIntoView({block: 'nearest'});
                    }
                });
            } else if (event.key === 'Enter' && !results.hidden && searchMatches.length) {
                event.preventDefault();
                select(searchMatches[Math.max(0, searchIndex)].id, true);
            }
        });
        document.addEventListener('click', function (event) { if (!event.target.closest('.gx-search-wrap')) hideSearch(); });
        document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && results.hidden && detailMode) clearSelection(); });
        topicPicker.addEventListener('change', function () { chooseTopic(topicPicker.value); });
        companyPicker.addEventListener('change', function () { chooseCompany(companyPicker.value); });
        byId('graphOverview').addEventListener('click', function () { map.overview(); });
        byId('graphZoomIn').addEventListener('click', function () { map.zoomBy(1.25); });
        byId('graphZoomOut').addEventListener('click', function () { map.zoomBy(0.8); });
        byId('graphPanelClose').addEventListener('click', clearSelection);
    }
    async function init() {
        try {
            const response = await fetch('/graph/data.json', {cache: 'no-cache'});
            if (!response.ok) throw new Error('graph data unavailable');
            network = (await response.json()).explorer.network;
            if (!network || !window.RecommendationMap) throw new Error('graph dependencies unavailable');
            nodes = new Map(network.nodes.map(function (node) { return [node.id, node]; }));
            links = new Map(network.links.map(function (link) { return [link.id, link]; }));
            relations = new Map(network.relations.map(function (relation) { return [relation.id, relation]; }));
            linksByNode = new Map(network.nodes.map(function (node) { return [node.id, []]; }));
            network.links.forEach(function (link) { linksByNode.get(link.source).push(link); linksByNode.get(link.target).push(link); });
            map = window.RecommendationMap.create(byId('graphCanvas'), {
                onSelect: function (id) { select(id, false); },
                onClear: clearSelection,
                onLinkSelect: selectLink,
                onViewportChange: function (viewport) { byId('graphZoom').textContent = Math.round(viewport.zoom * 100) + '%'; },
            });
            map.render(network);
            byId('graphCount').textContent = '已收录 ' + nodes.size + ' 篇论文';
            const companies = new Map();
            network.nodes.forEach(function (node) {
                const key = companyKey(node);
                companyNames.set(key, key === 'unattributed' ? '未标注公司' : String(node.company).trim());
                companies.set(key, (companies.get(key) || 0) + 1);
            });
            Array.from(companies).sort(function (a, b) {
                return Number(a[0] === 'unattributed') - Number(b[0] === 'unattributed') || companyNames.get(a[0]).localeCompare(companyNames.get(b[0]));
            }).forEach(function (entry) {
                const option = el('option', '', companyNames.get(entry[0]) + ' · ' + entry[1]);
                option.value = entry[0];
                companyPicker.append(option);
            });
            network.topics.forEach(function (topic) {
                const option = el('option', '', topic.label + ' · ' + topic.count);
                option.value = topic.id;
                topicPicker.append(option);
            });
            loading.hidden = true;
            wire();
            const focus = new URL(location.href).searchParams.get('focus');
            if (focus) {
                const exact = nodes.get(focus) || network.nodes.find(function (node) { return [label(node)].concat(node.aliases || []).some(function (name) { return normalize(name) === normalize(focus); }); });
                if (exact) select(exact.id, true, false);
                else { searchInput.value = focus; showSearch(); }
            }
            window.addEventListener('pagehide', function (event) { if (!event.persisted) map.destroy(); });
        } catch (error) {
            loading.replaceChildren();
            const message = el('div', 'gx-load-error', '图谱暂时未能加载。');
            message.append(button('重新加载', 'gx-button', function () { location.reload(); }));
            loading.append(message);
            byId('graphCount').textContent = '加载失败';
        }
    }
    init();
})();
