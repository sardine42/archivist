(function () {
    'use strict';
    const root = document.getElementById('graphExplorer');
    if (!root) return;
    const byId = function (id) { return document.getElementById(id); };
    const searchInput = byId('graphSearch');
    const results = byId('graphSearchResults');
    const panel = byId('graphPanelBody');
    const inspector = byId('graphInspector');
    const close = byId('graphPanelClose');
    const category = byId('graphCategory');
    const loading = byId('graphLoading');
    const categoryNames = {'generative-rec': '生成式', 'discriminative-rec': '判别式', llm: 'LLM', other: '其他'};
    let model, map, payload, selected = null, activeLineage = '', searchMatches = [], searchIndex = -1;
    let highlighted = null, expanded = false;

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
    function date(node) { return node.published_date || '日期未知'; }
    function label(node) { return node.model_name || node.title || node.id; }
    function allNewest(nodes) {
        return nodes.slice().sort(function (a, b) {
            return (b.published_date || '').localeCompare(a.published_date || '') || label(a).localeCompare(label(b));
        });
    }
    function badges(node) {
        const wrap = el('span', 'category-badges');
        node.category.forEach(function (type) {
            wrap.append(el('span', 'category-badge category-' + (categoryNames[type] ? type : 'other'), categoryNames[type] || type));
        });
        return wrap;
    }
    function nodeRow(node, description) {
        const row = button('', 'gx-node-row', function () { select(node.id); });
        const top = el('span', 'gx-row-top');
        const name = el('span', 'gx-row-title', label(node));
        top.append(name, el('time', 'gx-row-date', date(node)));
        row.append(top);
        row.append(el('span', 'gx-row-description', node.title === label(node) ? (node.summary || '') : node.title));
        if (description) row.append(el('span', 'gx-row-relation', description));
        if (!node.has_reading) row.append(el('span', 'gx-row-relation', '仅引用 · 未关联精读文档'));
        return row;
    }
    function setPanelLabel(value, canClose) {
        byId('graphPanelLabel').textContent = value;
        close.hidden = !canClose;
        inspector.classList.toggle('gx-detail-open', !!canClose && !!selected);
        inspector.scrollTop = 0;
    }
    function section(title) {
        const part = el('section', 'gx-section');
        part.append(el('h3', '', title));
        return part;
    }
    function memberships(node) {
        return model.lineages.filter(function (item) {
            return item.members.includes(node.id) || item.related_members.includes(node.id);
        });
    }
    function updateHighlight() {
        let nodes = model.readNodes;
        if (activeLineage) {
            const group = model.lineage(activeLineage);
            nodes = group.members.concat(group.related);
        }
        if (category.value) nodes = nodes.filter(function (node) { return node.category.includes(category.value); });
        highlighted = activeLineage || category.value ? nodes.map(function (node) { return node.id; }) : null;
        if (highlighted) map.showNodes(nodes);
        map.setHighlight(highlighted);
        category.dataset.category = category.value;
        byId('graphClear').hidden = !highlighted && !selected;
        root.querySelectorAll('.gx-lineage').forEach(function (item) {
            item.setAttribute('aria-pressed', item.dataset.lineage === activeLineage ? 'true' : 'false');
        });
        const group = model.lineages.find(function (item) { return item.id === activeLineage; });
        const paperCount = nodes.filter(function (node) { return node.has_reading; }).length;
        const referenceCount = nodes.length - paperCount;
        byId('graphContext').textContent = highlighted ? ((group ? group.label : '') + (category.value ? ' · ' + categoryNames[category.value] : '')).replace(/^ · /, '') + ' · ' + paperCount + ' 篇论文' + (referenceCount ? ' · ' + referenceCount + ' 个引用模型' : '') : '';
    }
    function showBrowse() {
        selected = null;
        expanded = false;
        map.setSelection(null, {relatedIds: [], similarIds: [], relations: []});
        panel.replaceChildren();
        const group = activeLineage ? model.lineage(activeLineage, {category: category.value}) : null;
        let nodes = group ? group.members : model.readNodes.filter(function (node) { return !category.value || node.category.includes(category.value); });
        const references = group ? nodes.filter(function (node) { return !node.has_reading; }) : [];
        nodes = allNewest(nodes.filter(function (node) { return node.has_reading; }));
        setPanelLabel(group ? group.definition.label + ' · ' + nodes.length + ' 篇' : '最新发表', !!group || !!category.value);
        const list = el('div', 'gx-node-list');
        const maximum = group ? nodes.length : 16;
        nodes.slice(0, maximum).forEach(function (node) { list.append(nodeRow(node)); });
        if (!nodes.length) list.append(el('p', 'gx-empty', '这个范围内暂时没有论文。'));
        panel.append(list);
        if (group && group.related.length) {
            const related = section('系列扩展');
            group.related.forEach(function (node) { related.append(nodeRow(node)); });
            panel.append(related);
        }
        if (references.length) {
            const refs = section('引用模型');
            references.forEach(function (node) { refs.append(nodeRow(node)); });
            panel.append(refs);
        }
        updateHighlight();
    }
    function relationLabel(relation, focus) {
        if (relation.type === 'citation') return relation.source === focus.id ? '本篇引用' : '引用本篇';
        return relation.target === focus.id ? '与本篇比较的基线' : '实验中报告优于本篇';
    }
    function addEvidence(focus, relations) {
        if (!relations.length) return;
        const part = section('关系依据');
        relations.forEach(function (relation) {
            const from = model.nodes.get(relation.source), to = model.nodes.get(relation.target);
            const details = el('details', 'gx-evidence');
            const title = relation.type === 'citation' ? label(from) + ' 引用 ' + label(to) : label(from) + ' → ' + label(to) + ' · 实验比较';
            details.append(el('summary', '', title));
            details.append(el('p', '', relation.summary || (relation.type === 'citation' ? '论文中记录的引用关系。' : '论文中报告的实验比较，结果取决于数据集与实验设置。')));
            if (relation.type === 'comparison' && relation.is_self_reported) details.append(el('p', '', '由论文作者报告'));
            if (relation.datasets && Object.keys(relation.datasets).length) details.append(el('p', '', '数据集：' + Object.keys(relation.datasets).join('、')));
            const source = model.resolve('paper:' + (relation.paper_id || '').replace(/v\d+$/, ''));
            if (source && source.reading_url) {
                const sourceLink = el('a', '', '来源：' + label(source) + ' ↗');
                sourceLink.href = source.reading_url;
                details.append(sourceLink);
            }
            part.append(details);
        });
        panel.append(part);
    }
    function renderSelected(node) {
        panel.replaceChildren();
        setPanelLabel('论文与相关研究', true);
        const title = el('h2', 'gx-panel-title');
        // title_html is produced by the shared, escaping server-side formatter.
        title.innerHTML = node.title_html;
        panel.append(title);
        const meta = el('div', 'gx-panel-meta');
        meta.append(badges(node), el('time', '', date(node)));
        if (node.company) meta.append(el('span', '', node.company));
        if (node.reading_score > 0) meta.append(el('span', '', '精读 ' + node.reading_score));
        panel.append(meta);
        if (node.summary) {
            const summary = el('p', 'gx-summary', node.summary);
            summary.id = 'gx-paper-summary';
            panel.append(summary);
            if (summary.scrollHeight > summary.clientHeight + 2) {
                const toggle = button('展开摘要', 'gx-summary-toggle', function () {
                    const isExpanded = summary.classList.toggle('gx-summary-expanded');
                    toggle.textContent = isExpanded ? '收起摘要' : '展开摘要';
                    toggle.setAttribute('aria-expanded', String(isExpanded));
                });
                toggle.setAttribute('aria-controls', summary.id);
                toggle.setAttribute('aria-expanded', 'false');
                panel.append(toggle);
            }
        }
        const links = el('div', 'gx-panel-links');
        if (node.reading_url) {
            const reading = el('a', '', '阅读全文 ↗');
            reading.href = node.reading_url;
            links.append(reading);
        }
        if (node.arxiv_url) {
            const source = el('a', '', 'arXiv ↗');
            source.href = node.arxiv_url;
            source.target = '_blank';
            source.rel = 'noopener noreferrer';
            links.append(source);
        }
        if (links.childElementCount) panel.append(links);
        if (!node.has_reading) panel.append(el('p', 'gx-panel-intro', '这是其他论文中出现的引用模型，尚未关联精读文档；未知日期不代表最新研究。'));
        const groups = memberships(node);
        if (groups.length) {
            const tags = el('div', 'gx-lineages');
            groups.forEach(function (group) {
                tags.append(button(group.label + ' 谱系', 'gx-lineage', function () { chooseLineage(group.id); }));
            });
            panel.append(tags);
        }
        const related = model.related(node.id, {limitPerSide: expanded ? model.nodes.size : 8});
        const neighbors = related.before.concat(related.after);
        const relations = Array.from(new Map(neighbors.flatMap(function (item) { return item.relations; }).map(function (relation) { return [relation.id, relation]; })).values());
        map.showNodes([node].concat(neighbors.map(function (item) { return item.node; })));
        map.setSelection(node.id, {
            relatedIds: neighbors.map(function (item) { return item.node.id; }),
            similarIds: related.similar.map(function (item) { return item.node.id; }),
            relations: relations,
        });
        const total = related.beforeTotal + related.afterTotal;
        const direct = section('相关论文' + (total ? ' · ' + total : ''));
        if (neighbors.length) {
            neighbors.forEach(function (item) {
                const labels = Array.from(new Set(item.relations.map(function (relation) { return relationLabel(relation, node); })));
                direct.append(nodeRow(item.node, labels.join(' · ')));
            });
            if (neighbors.length < total) direct.append(button('展开全部 ' + total + ' 篇关联论文', 'gx-more', function () { expanded = true; renderSelected(node); }));
            direct.append(el('p', 'gx-section-note', '实线箭头指向实验中报告较优的模型；虚线箭头指向被引用论文。关系不等同于继承或统一排名。'));
        } else direct.append(el('p', 'gx-empty', '暂未记录直接引用或实验比较关系。'));
        panel.append(direct);
        if (related.similar.length) {
            const similar = section('同主题论文');
            related.similar.forEach(function (item) {
                const labels = item.sharedLineages.map(function (group) { return group.label; }).concat(item.sharedTags.slice(0, 2));
                similar.append(nodeRow(item.node, Array.from(new Set(labels)).join(' · ')));
            });
            similar.append(el('p', 'gx-section-note', '依据共同谱系或主题标签推荐，不绘制推断关系线。'));
            panel.append(similar);
        }
        if (node.tags.length) {
            const topics = section('主题标签');
            const tags = el('div', 'gx-tags');
            node.tags.forEach(function (tag) { tags.append(el('span', 'gx-tag', tag)); });
            topics.append(tags);
            panel.append(topics);
        }
        addEvidence(node, relations);
    }
    function select(value, updateURL) {
        const node = model.resolve(value);
        if (!node) return;
        selected = node.id;
        expanded = false;
        hideSearch();
        if (highlighted && !highlighted.includes(node.id)) {
            activeLineage = '';
            category.value = '';
            updateHighlight();
        }
        renderSelected(node);
        map.focus(node.id, {fitRelated: true});
        if (window.matchMedia('(max-width: 760px)').matches) {
            byId('graphCanvas').scrollIntoView({block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
        }
        byId('graphClear').hidden = false;
        if (updateURL !== false) {
            const url = new URL(location.href);
            url.searchParams.set('focus', node.id);
            history.replaceState(null, '', url);
        }
    }
    function clearURL() {
        const url = new URL(location.href);
        url.searchParams.delete('focus');
        history.replaceState(null, '', url);
    }
    function chooseLineage(id) {
        activeLineage = activeLineage === id ? '' : id;
        clearURL();
        showBrowse();
        if (activeLineage) {
            const group = model.lineage(activeLineage, {category: category.value});
            const latest = allNewest(group.members.concat(group.related))[0];
            if (latest) map.focus(latest.id, {align: 'right', fitHighlight: true});
        }
    }
    function hideSearch() {
        results.hidden = true;
        searchInput.setAttribute('aria-expanded', 'false');
        searchInput.removeAttribute('aria-activedescendant');
        searchIndex = -1;
    }
    function updateSearchIndex(index) {
        searchIndex = index;
        results.querySelectorAll('[role="option"]').forEach(function (option, i) {
            option.setAttribute('aria-selected', String(i === index));
            if (i === index) {
                searchInput.setAttribute('aria-activedescendant', option.id);
                option.scrollIntoView({block: 'nearest'});
            }
        });
    }
    function showSearch() {
        if (!model) return;
        results.replaceChildren();
        searchMatches = model.search(searchInput.value, 8);
        if (!searchInput.value.trim()) { hideSearch(); return; }
        searchIndex = -1;
        searchInput.removeAttribute('aria-activedescendant');
        searchMatches.forEach(function (node, index) {
            const option = button('', 'gx-search-option', function () { select(node.id); });
            option.id = 'gx-search-option-' + index;
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', 'false');
            const top = el('span', 'gx-row-top');
            top.append(el('span', 'gx-row-title', label(node)), el('span', 'gx-row-date', date(node)));
            option.append(top, el('span', 'gx-row-description', node.title));
            option.append(el('span', 'gx-row-relation', node.has_reading ? '已精读' : '仅引用 · 未关联精读文档'));
            results.append(option);
        });
        if (!searchMatches.length) results.append(el('p', 'gx-search-empty', '没有找到相关论文，请尝试模型名或其他关键词。'));
        results.hidden = false;
        searchInput.setAttribute('aria-expanded', 'true');
    }
    function latest() {
        activeLineage = '';
        category.value = '';
        clearURL();
        showBrowse();
        map.reset();
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
                const next = searchIndex < 0 ? (event.key === 'ArrowDown' ? 0 : searchMatches.length - 1) : searchIndex + (event.key === 'ArrowDown' ? 1 : -1);
                updateSearchIndex((next + searchMatches.length) % searchMatches.length);
            } else if (event.key === 'Enter' && !results.hidden && searchMatches.length) {
                event.preventDefault();
                select(searchMatches[Math.max(0, searchIndex)].id);
            }
        });
        document.addEventListener('click', function (event) { if (!event.target.closest('.gx-search-wrap')) hideSearch(); });
        document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && results.hidden && selected) { clearURL(); showBrowse(); } });
        byId('graphLatest').addEventListener('click', latest);
        byId('graphOverview').addEventListener('click', function () { map.overview(); });
        byId('graphZoomIn').addEventListener('click', function () { map.zoomBy(1.3); });
        byId('graphZoomOut').addEventListener('click', function () { map.zoomBy(1 / 1.3); });
        byId('graphClear').addEventListener('click', function () { activeLineage = ''; category.value = ''; clearURL(); showBrowse(); });
        close.addEventListener('click', function () { if (!selected) { activeLineage = ''; category.value = ''; } clearURL(); showBrowse(); });
        category.addEventListener('change', function () { clearURL(); showBrowse(); });
    }
    function error() {
        loading.replaceChildren();
        const message = el('div', 'gx-load-error', '图谱暂时未能加载，请重试。');
        message.append(button('重新加载', '', function () { location.reload(); }));
        loading.append(message);
        byId('graphCount').textContent = '加载失败';
    }
    async function init() {
        try {
            const response = await fetch('/graph/data.json');
            if (!response.ok) throw new Error('graph data unavailable');
            const data = await response.json();
            payload = data.explorer;
            if (!payload || !window.GraphExplorerModel || !window.GraphMap || !window.cytoscape) throw new Error('graph dependencies unavailable');
            model = window.GraphExplorerModel.create(payload);
            map = window.GraphMap.create(byId('graphCanvas'), {
                onSelect: select,
                onViewportChange: function (viewport) {
                    const zoom = viewport && (viewport.zoom || viewport.scale);
                    if (Number.isFinite(zoom)) byId('graphZoom').textContent = Math.round(zoom * 100) + '%';
                },
            });
            map.render({nodes: model.readNodes, relations: payload.relations, lineages: model.lineages, asOf: payload.as_of});
            byId('graphCount').textContent = '已收录 ' + model.readNodes.length + ' 篇论文';
            model.lineages.forEach(function (group) {
                const count = group.members.filter(function (id) { return model.nodes.get(id) && model.nodes.get(id).has_reading; }).length;
                const chip = button(group.label, 'gx-lineage', function () { chooseLineage(group.id); });
                chip.dataset.lineage = group.id;
                chip.setAttribute('aria-pressed', 'false');
                chip.title = group.description;
                chip.append(el('span', '', String(count)));
                byId('graphLineages').append(chip);
            });
            wire();
            showBrowse();
            loading.hidden = true;
            const focus = new URLSearchParams(location.search).get('focus');
            if (focus) {
                const normalized = focus.normalize('NFKC').trim().toLocaleLowerCase();
                const exact = Array.from(model.nodes.values()).filter(function (item) {
                    return [item.model_name].concat(item.aliases).some(function (name) {
                        return String(name).normalize('NFKC').trim().toLocaleLowerCase() === normalized;
                    });
                });
                if (!focus.startsWith('paper:') && !focus.startsWith('model:') && exact.length > 1) {
                    searchInput.value = focus;
                    showSearch();
                    byId('graphContext').textContent = '同名模型有多篇论文，请选择搜索结果';
                } else {
                    const node = model.resolve(focus);
                    if (node) select(node.id, false);
                    else byId('graphContext').textContent = '未找到指定论文，请尝试搜索';
                }
            }
        } catch (reason) { error(); }
    }
    init();
})();
