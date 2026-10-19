(function (global) {
    'use strict';
    const DAY = 86400000, STEP = 420, ORIGIN = 420, UNKNOWN = 120, WIDTH = 180, HEIGHT = 66, ROW = 84;
    const LABELS = {'generative-rec': '生成式推荐', 'discriminative-rec': '判别式推荐', llm: 'LLM', other: '其他研究'};
    const COLORS = {'generative-rec': ['#edf2f8', '#b9cce0'], 'discriminative-rec': ['#f7f0ea', '#ddc7b6'], llm: ['#f3eff8', '#d0c0df'], other: ['#edf4f2', '#bfd7d0']};
    function el(tag, cls, text) { const item = document.createElement(tag); if (cls) item.className = cls; if (text != null) item.textContent = text; return item; }
    function id(node) { return String(node.id || node.model_name || ''); }
    function date(value) {
        const valueText = String(value || '').slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(valueText)) return null;
        const stamp = Date.parse(valueText + 'T00:00:00Z');
        return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 10) === valueText ? stamp : null;
    }
    function month(stamp) {
        const d = new Date(stamp), y = d.getUTCFullYear(), m = d.getUTCMonth();
        return y * 12 + m + (stamp - Date.UTC(y, m, 1)) / (Date.UTC(y, m + 1, 1) - Date.UTC(y, m, 1));
    }
    function monthLabel(value) { const y = Math.floor(value / 12); return y + '-' + String(value - y * 12 + 1).padStart(2, '0'); }
    function short(value) { const chars = Array.from(String(value)); return chars.length > 40 ? chars.slice(0, 39).join('') + '…' : chars.join(''); }

    function create(container, options) {
        if (!container || !container.appendChild) throw new TypeError('GraphMap requires a container');
        if (typeof global.cytoscape !== 'function') throw new Error('Cytoscape must load before GraphMap');
        const callbacks = options || {}, root = el('div', 'gm-map'), grid = el('div', 'gm-grid'), canvas = el('div', 'gm-canvas');
        const axis = el('div', 'gm-time-axis'), labels = el('div', 'gm-lane-labels');
        const notice = el('div', 'gm-low-zoom', '全局概览 · 点击小地图或定位最新，恢复可读字号');
        const tooltip = el('div', 'gm-tooltip'), mini = el('div', 'gm-mini'), miniButton = el('button', 'gm-mini-button'), miniCanvas = el('canvas', 'gm-mini-canvas');
        const keyboard = el('select', 'gm-keyboard-select');
        canvas.tabIndex = 0;
        canvas.setAttribute('role', 'application');
        canvas.setAttribute('aria-label', '论文时间图。方向键平移，加减键缩放，End 键定位最新论文。');
        [grid, axis, labels].forEach(function (item) { item.setAttribute('aria-hidden', 'true'); });
        tooltip.hidden = true; tooltip.setAttribute('role', 'tooltip'); notice.hidden = true;
        miniButton.type = 'button'; miniButton.setAttribute('aria-label', '全局小地图，点击定位；方向键平移，End 键定位最新');
        miniButton.appendChild(miniCanvas);
        mini.append(el('div', 'gm-mini-heading', '全局定位'), miniButton, el('div', 'gm-mini-hint', '点击地图跳转 · 滚轮缩放'));
        keyboard.setAttribute('aria-label', '用键盘选择并定位论文节点');
        root.append(grid, canvas, axis, labels, notice, mini, keyboard, tooltip); container.appendChild(root);
        let cy = null, destroyed = false, frame = 0, nodes = new Map(), positions = new Map(), memberships = new Map();
        let lanes = [], lanesById = new Map(), months = [], relations = [], initialIds = [], selected = '';
        let related = new Set(), similar = new Set(), highlighted = null, asOf = null, latest = null, miniTransform = null, compact = false;
        let bounds = {x1: 0, x2: 1000, y1: 0, y2: 500};

        function xFor(stamp) {
            if (stamp == null || !months.length) return UNKNOWN;
            const value = month(stamp); let index = 0, distance;
            while (index + 1 < months.length && months[index + 1] <= value) index += 1;
            if (value < months[0]) distance = value - months[0];
            else if (index === months.length - 1) distance = index + value - months[index];
            else distance = index + (value - months[index]) / (months[index + 1] - months[index]);
            return ORIGIN + distance * STEP;
        }
        function laneKey(node) {
            const lineage = memberships.get(id(node));
            if (lineage) return 'lineage:' + lineage.id;
            const category = (Array.isArray(node.category) ? node.category[0] : node.category) || 'other';
            return 'category:' + (LABELS[category] ? category : 'other');
        }
        function place(node, lane, limit) {
            const stamp = date(node.published_date), x = xFor(stamp); let row = 0;
            while (row < (limit == null ? Infinity : limit) && (lane.rows[row] || []).some(function (other) { return Math.abs(other - x) < WIDTH + 20; })) row += 1;
            if (limit != null && row >= limit) return false;
            if (!lane.rows[row]) lane.rows[row] = [];
            lane.rows[row].push(x); positions.set(id(node), {x: x, y: lane.top + 54 + row * ROW, laneId: lane.id, date: stamp}); return true;
        }
        function updateBounds() {
            const points = Array.from(positions.values());
            bounds = {x1: Math.min(0, ...points.map(function (p) { return p.x - WIDTH / 2 - 30; })), x2: Math.max(700, ...points.map(function (p) { return p.x + WIDTH / 2 + 45; })), y1: 0, y2: Math.max(500, ...lanes.map(function (lane) { return lane.bottom; }))};
            latest = Math.max(...Array.from(nodes.values()).map(function (node) { return date(node.published_date); }).filter(function (stamp) { return stamp != null; }));
            if (!Number.isFinite(latest)) latest = null;
        }
        function layout(definitions) {
            positions = new Map(); memberships = new Map();
            months = Array.from(new Set(Array.from(nodes.values()).map(function (node) { const stamp = date(node.published_date); return stamp == null ? null : Math.floor(month(stamp)); }).filter(function (m) { return m != null; }))).sort(function (a, b) { return a - b; });
            const sorted = (definitions || []).slice().sort(function (a, b) { return Number(b.kind === 'series') - Number(a.kind === 'series'); });
            sorted.forEach(function (definition) { (definition.members || definition.node_ids || definition.nodes || []).forEach(function (member) { const key = typeof member === 'object' ? id(member) : String(member); if (!memberships.has(key)) memberships.set(key, definition); }); });
            const used = new Set(Array.from(nodes.values()).map(laneKey));
            const ordered = sorted.filter(function (definition) { return used.has('lineage:' + definition.id); }).map(function (definition) { return {id: 'lineage:' + definition.id, label: String(definition.label || definition.id) + (definition.kind === 'series' ? ' 系列' : ''), kind: definition.kind}; });
            Object.keys(LABELS).forEach(function (category) { if (used.has('category:' + category)) ordered.push({id: 'category:' + category, label: LABELS[category], kind: 'category'}); });
            let top = 34;
            lanes = ordered.map(function (definition) {
                const lane = Object.assign(definition, {top: top, bottom: 0, rows: [], capacity: 0});
                Array.from(nodes.values()).filter(function (node) { return laneKey(node) === lane.id; }).sort(function (a, b) { return (date(a.published_date) || -Infinity) - (date(b.published_date) || -Infinity) || id(a).localeCompare(id(b)); }).forEach(function (node) { place(node, lane); });
                lane.capacity = Math.max(1, lane.rows.length); lane.bottom = lane.top + 32 + lane.capacity * ROW; top = lane.bottom + 16; return lane;
            });
            lanesById = new Map(lanes.map(function (lane) { return [lane.id, lane]; })); updateBounds();
        }
        function nodeElement(node) {
            const stamp = date(node.published_date), recent = stamp != null && asOf != null && stamp <= asOf && stamp >= asOf - 89 * DAY && Number(node.reading_score) >= 8;
            const category = (Array.isArray(node.category) ? node.category[0] : node.category) || 'other', colors = COLORS[category] || COLORS.other;
            return {group: 'nodes', data: {id: id(node), label: short(node.model_name || node.title || id(node)) + '\n' + (stamp == null ? '日期未知' : new Date(stamp).toISOString().slice(0, 10)) + (recent ? '  ★' : ''), fill: node.has_reading ? colors[0] : '#fafbfc', border: recent ? '#b19550' : (node.has_reading ? colors[1] : '#c7cdd5'), borderStyle: node.has_reading ? 'solid' : 'dashed', borderWidth: recent ? 2 : 1}, position: positions.get(id(node))};
        }
        function schedule() { if (!destroyed && cy && !frame) frame = global.requestAnimationFrame(function () { frame = 0; overlay(); }); }
        function drawMini() {
            const w = miniCanvas.clientWidth || 200, h = miniCanvas.clientHeight || 100, ratio = Math.min(2, global.devicePixelRatio || 1);
            miniCanvas.width = w * ratio; miniCanvas.height = h * ratio;
            const context = miniCanvas.getContext('2d'); if (!context) return;
            context.scale(ratio, ratio); context.fillStyle = '#fff'; context.fillRect(0, 0, w, h);
            const scale = Math.min((w - 12) / (bounds.x2 - bounds.x1), (h - 12) / (bounds.y2 - bounds.y1));
            const left = (w - (bounds.x2 - bounds.x1) * scale) / 2, top = (h - (bounds.y2 - bounds.y1) * scale) / 2;
            miniTransform = {scale: scale, left: left, top: top};
            const active = [];
            positions.forEach(function (point, key) {
                if (highlighted && highlighted.has(key)) active.push({point: point, id: key});
                context.fillStyle = highlighted ? '#d3dae4' : '#a5b1c1';
                context.fillRect(left + (point.x - bounds.x1) * scale - .75, top + (point.y - bounds.y1) * scale - .75, 1.5, 1.5);
            });
            const groups = new Map();
            active.forEach(function (entry) { if (!groups.has(entry.point.laneId)) groups.set(entry.point.laneId, []); groups.get(entry.point.laneId).push(entry.point); });
            groups.forEach(function (points) {
                const xs = points.map(function (p) { return left + (p.x - bounds.x1) * scale; });
                const ys = points.map(function (p) { return top + (p.y - bounds.y1) * scale; });
                context.fillStyle = 'rgba(59,95,148,.13)';
                context.fillRect(Math.min(...xs) - 4, Math.min(...ys) - 4, Math.max(8, Math.max(...xs) - Math.min(...xs) + 8), Math.max(8, Math.max(...ys) - Math.min(...ys) + 8));
            });
            active.forEach(function (entry) {
                context.fillStyle = '#315f96';
                context.fillRect(left + (entry.point.x - bounds.x1) * scale - 2.5, top + (entry.point.y - bounds.y1) * scale - 2.5, 5, 5);
            });
            const selectedPoint = positions.get(selected);
            if (selectedPoint) { context.fillStyle = '#b23c28'; context.fillRect(left + (selectedPoint.x - bounds.x1) * scale - 3, top + (selectedPoint.y - bounds.y1) * scale - 3, 6, 6); }
            let family = '';
            if (groups.size) { const main = Array.from(groups.entries()).sort(function (a, b) { return b[1].length - a[1].length; })[0]; const lane = lanesById.get(main[0]); if (lane) family = ' · ' + lane.label; }
            mini.querySelector('.gm-mini-heading').textContent = '全局定位' + family;
            const extent = cy.extent(), x = left + (extent.x1 - bounds.x1) * scale, y = top + (extent.y1 - bounds.y1) * scale, rw = Math.max(3, extent.w * scale), rh = Math.max(3, extent.h * scale);
            context.strokeStyle = '#3b5f94'; context.lineWidth = 1.5; context.fillStyle = 'rgba(59,95,148,.08)'; context.fillRect(x, y, rw, rh); context.strokeRect(x, y, rw, rh);
        }
        function overlay() {
            if (!cy || destroyed) return;
            const zoom = cy.zoom(), pan = cy.pan(), width = canvas.clientWidth, height = canvas.clientHeight;
            if (compact !== (zoom < .55)) { compact = zoom < .55; cy.nodes().toggleClass('gm-compact', compact); }
            axis.replaceChildren(); grid.replaceChildren(); labels.replaceChildren();
            let shownYear = '';
            months.forEach(function (m, index) {
                const left = (ORIGIN + index * STEP) * zoom + pan.x;
                const nextLeft = index + 1 < months.length ? (ORIGIN + (index + 1) * STEP) * zoom + pan.x : Infinity;
                if ((left < -85 && nextLeft <= 0) || left > width + 20) return;
                if (left < 0 && nextLeft > 0 && nextLeft < 90) return;
                const year = monthLabel(m).slice(0, 4);
                if (zoom < .48 && year === shownYear) return;
                shownYear = year;
                const text = el('span', 'gm-time-label', zoom >= .48 ? monthLabel(m) : monthLabel(m).slice(0, 4)); text.style.left = Math.max(0, left) + 'px'; axis.appendChild(text);
                if (left >= 0) { const line = el('span', 'gm-date-line'); line.style.left = left + 'px'; grid.appendChild(line); }
            });
            const unknownX = UNKNOWN * zoom + pan.x;
            if (unknownX > -100 && unknownX < width) { const item = el('span', 'gm-time-label gm-time-label--unknown', '日期未知'); item.style.left = unknownX - 28 + 'px'; axis.appendChild(item); }
            lanes.forEach(function (lane, index) {
                const top = lane.top * zoom + pan.y, bottom = lane.bottom * zoom + pan.y;
                if (bottom < 38 || top > height) return;
                const band = el('div', 'gm-lane-band' + (index % 2 ? ' gm-lane-band--alternate' : '')); band.style.top = Math.max(38, top) + 'px'; band.style.height = Math.max(0, Math.min(height, bottom) - Math.max(38, top)) + 'px'; grid.appendChild(band);
                if (zoom >= .32) { const item = el('span', 'gm-lane-label', lane.label); item.style.top = Math.max(43, top + 5) + 'px'; labels.appendChild(item); }
            });
            notice.hidden = zoom >= .55; drawMini();
            if (typeof callbacks.onViewportChange === 'function') callbacks.onViewportChange(getViewportInfo());
        }
        function emphasis() {
            if (!cy) return;
            cy.batch(function () { cy.nodes().forEach(function (node) {
                const key = node.id(), isSelected = key === selected, isRelated = related.has(key), isSimilar = similar.has(key), isHighlighted = !!highlighted && highlighted.has(key);
                node.toggleClass('gm-selected', isSelected); node.toggleClass('gm-related', isRelated); node.toggleClass('gm-similar', isSimilar); node.toggleClass('gm-highlighted', isHighlighted);
                node.toggleClass('gm-dimmed', selected ? !isSelected && !isRelated && !isSimilar : highlighted != null && !isHighlighted);
            }); }); keyboard.value = selected; schedule();
        }
        function setSelection(value, settings) {
            if (!cy || destroyed) return;
            const config = settings || {}; selected = value == null ? '' : String(value); related = new Set((config.relatedIds || []).map(String)); similar = new Set((config.similarIds || []).map(String));
            const edges = Object.prototype.hasOwnProperty.call(config, 'relations') ? config.relations || [] : relations;
            cy.batch(function () { cy.edges().remove(); const seen = new Set(); edges.forEach(function (edge, index) {
                const source = String(edge.source), target = String(edge.target), key = source + '\u0000' + target + '\u0000' + edge.type;
                if (!selected || (source !== selected && target !== selected) || !nodes.has(source) || !nodes.has(target) || source === target || seen.has(key) || !['citation', 'comparison'].includes(edge.type)) return;
                seen.add(key); related.add(source === selected ? target : source); cy.add({group: 'edges', data: {id: '__gm_relation_' + index, source: source, target: target, type: edge.type}});
            }); }); emphasis();
        }
        function setHighlight(values) { highlighted = values == null ? null : new Set(Array.from(values).map(String)); emphasis(); }
        function panTo(point, zoom, animate) {
            if (!cy || !point) return;
            const view = {zoom: zoom, pan: {x: canvas.clientWidth / 2 - point.x * zoom, y: (canvas.clientHeight + 38) / 2 - point.y * zoom}};
            if (animate && !global.matchMedia('(prefers-reduced-motion: reduce)').matches) cy.animate(view, {duration: 220}); else cy.viewport(view);
        }
        function focus(value, settings) {
            if (!cy || destroyed || !positions.has(String(value))) return;
            cy.minZoom(.15); cy.stop();
            const point = positions.get(String(value)), zoom = Math.max(.9, cy.zoom());
            const target = settings && settings.align === 'right' ? {x: point.x - canvas.clientWidth / (2 * zoom) + WIDTH / 2 + 52 / zoom, y: point.y} : point;
            panTo(target, zoom, true);
        }
        function reset() {
            if (!cy || destroyed || !positions.size) return;
            cy.minZoom(.15); cy.stop();
            const dated = initialIds.map(function (key) { return positions.get(key); }).filter(function (point) { return point && point.date != null; }).sort(function (a, b) { return b.date - a.date || a.y - b.y; });
            if (!dated.length) { panTo(positions.values().next().value, .9, false); return; }
            const ys = dated.slice(0, 20).map(function (p) { return p.y; }).sort(function (a, b) { return a - b; }), newest = dated[0], reach = canvas.clientHeight * .3 / .9;
            const y = Math.max(newest.y - reach, Math.min(newest.y + reach, ys[Math.floor(ys.length / 2)]));
            const x = newest.x - canvas.clientWidth / (2 * .9) + WIDTH / 2 + 52 / .9;
            panTo({x: x, y: y}, .9, false);
        }
        function zoomBy(factor) { if (!cy || destroyed || !(Number(factor) > 0)) return; cy.minZoom(.15); cy.zoom({level: Math.max(.15, Math.min(2, cy.zoom() * Number(factor))), renderedPosition: {x: canvas.clientWidth / 2, y: canvas.clientHeight / 2}}); }
        function overview() { if (!cy || destroyed || !cy.nodes().length) return; cy.stop(); cy.minZoom(.02); cy.fit(cy.nodes(), 45); }
        function syncKeyboard() {
            const fragment = document.createDocumentFragment(), first = el('option', null, '选择论文节点…'); first.value = ''; fragment.appendChild(first);
            Array.from(nodes.values()).sort(function (a, b) { return (date(b.published_date) || -Infinity) - (date(a.published_date) || -Infinity) || id(a).localeCompare(id(b)); }).forEach(function (node) { const option = el('option', null, String(node.published_date || '日期未知') + ' · ' + String(node.model_name || node.title || id(node))); option.value = id(node); fragment.appendChild(option); }); keyboard.replaceChildren(fragment); keyboard.value = selected;
        }
        function showNodes(values) {
            if (!cy || destroyed) return;
            const additions = []; (values || []).forEach(function (node) {
                const key = id(node); if (!key || nodes.has(key)) return; nodes.set(key, node);
                let lane = lanesById.get(laneKey(node));
                if (!lane || !place(node, lane, lane.capacity)) {
                    lane = lanesById.get('references');
                    if (!lane) { const top = Math.max(34, ...lanes.map(function (item) { return item.bottom + 16; })); lane = {id: 'references', label: '引用补充', kind: 'references', top: top, bottom: top + 116, rows: [], capacity: Infinity}; lanesById.set(lane.id, lane); lanes.push(lane); }
                    place(node, lane); lane.bottom = lane.top + 32 + Math.max(1, lane.rows.length) * ROW;
                }
                additions.push(nodeElement(node));
            });
            if (additions.length) { cy.add(additions); if (compact) cy.nodes().toggleClass('gm-compact', true); updateBounds(); syncKeyboard(); emphasis(); }
        }
        function render(payload) {
            if (destroyed) return; if (cy) { cy.destroy(); cy = null; }
            const data = payload || {}; nodes = new Map(); (data.nodes || []).forEach(function (node) { if (id(node)) nodes.set(id(node), node); }); initialIds = Array.from(nodes.keys()); relations = data.relations || []; selected = ''; related = new Set(); similar = new Set(); highlighted = null; compact = false; asOf = date(data.asOf || data.as_of); layout(data.lineages || []);
            cy = global.cytoscape({container: canvas, elements: Array.from(nodes.values()).map(nodeElement), layout: {name: 'preset', fit: false}, style: [
                {selector: 'node', style: {label: 'data(label)', shape: 'roundrectangle', width: WIDTH, height: HEIGHT, 'background-color': 'data(fill)', 'border-color': 'data(border)', 'border-width': 'data(borderWidth)', 'border-style': 'data(borderStyle)', color: '#733f35', 'font-size': 14.5, 'font-weight': 500, 'font-family': '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif', 'text-valign': 'center', 'text-halign': 'center', 'text-wrap': 'wrap', 'text-max-width': WIDTH - 16, 'text-overflow-wrap': 'anywhere', 'line-height': 1.3, 'overlay-opacity': 0}},
                {selector: 'node.gm-compact', style: {'text-opacity': 0}}, {selector: 'node.gm-dimmed', style: {opacity: .18}}, {selector: 'node.gm-highlighted', style: {'border-color': '#3b5f94', 'border-width': 2.5}}, {selector: 'node.gm-similar', style: {'border-color': '#558a7d', 'border-width': 2}}, {selector: 'node.gm-related', style: {'border-color': '#7795b9', 'border-width': 2}}, {selector: 'node.gm-selected', style: {'background-color': '#fff', 'border-color': '#b23c28', 'border-width': 3, 'font-weight': 700}},
                {selector: 'edge', style: {width: 1.6, 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'arrow-scale': 1, opacity: .8, 'overlay-opacity': 0}}, {selector: 'edge[type="comparison"]', style: {'line-color': '#7c92aa', 'target-arrow-color': '#7c92aa', 'line-style': 'solid'}}, {selector: 'edge[type="citation"]', style: {'line-color': '#aa8db7', 'target-arrow-color': '#aa8db7', 'line-style': 'dashed', 'line-dash-pattern': [6, 4]}}
            ], minZoom: .15, maxZoom: 2, zoom: .9, wheelSensitivity: .25, pixelRatio: Math.min(2, global.devicePixelRatio || 1), boxSelectionEnabled: false, autoungrabify: true, selectionType: 'single'});
            cy.on('pan zoom resize', function () { tooltip.hidden = true; schedule(); });
            cy.on('tap', 'node', function (event) { const key = event.target.id(); if (typeof callbacks.onSelect === 'function') callbacks.onSelect(key); else setSelection(key); });
            cy.on('mouseover', 'node', function (event) { const node = nodes.get(event.target.id()); if (!node) return; tooltip.textContent = String(node.title || node.model_name || node.id) + '\n' + String(node.published_date || '日期未知'); const p = event.target.renderedPosition(); tooltip.style.left = Math.max(8, Math.min(canvas.clientWidth - 288, p.x + 18)) + 'px'; tooltip.style.top = Math.max(45, Math.min(canvas.clientHeight - 94, p.y + 25)) + 'px'; tooltip.hidden = false; });
            cy.on('mouseout', 'node', function () { tooltip.hidden = true; }); syncKeyboard(); reset(); schedule();
        }
        function getLayoutInfo() { return {nodeCount: nodes.size, lanes: lanes.map(function (lane) { return {id: lane.id, label: lane.label, top: lane.top, bottom: lane.bottom}; }), months: months.map(monthLabel), latestDate: latest == null ? '' : new Date(latest).toISOString().slice(0, 10), bounds: Object.assign({}, bounds)}; }
        function getViewportInfo() { if (!cy) return {zoom: .9, nodeCount: 0, visibleNodes: 0}; const extent = cy.extent(); return {zoom: cy.zoom(), nodeCount: nodes.size, selectedId: selected, visibleNodes: Array.from(positions.values()).filter(function (p) { return p.x >= extent.x1 && p.x <= extent.x2 && p.y >= extent.y1 && p.y <= extent.y2; }).length, latestDate: latest == null ? '' : new Date(latest).toISOString().slice(0, 10), extent: {x1: extent.x1, x2: extent.x2, y1: extent.y1, y2: extent.y2}}; }
        function miniClick(event) { if (!cy || !miniTransform) return; if (event.detail === 0) { overview(); return; } const rect = miniCanvas.getBoundingClientRect(); cy.minZoom(.15); panTo({x: (event.clientX - rect.left - miniTransform.left) / miniTransform.scale + bounds.x1, y: (event.clientY - rect.top - miniTransform.top) / miniTransform.scale + bounds.y1}, Math.max(.9, cy.zoom()), false); }
        function keydown(event) {
            if (!cy || event.altKey || event.ctrlKey || event.metaKey) return;
            const moves = {ArrowLeft: [70, 0], ArrowRight: [-70, 0], ArrowUp: [0, 70], ArrowDown: [0, -70]};
            if (moves[event.key]) { event.preventDefault(); cy.panBy({x: moves[event.key][0], y: moves[event.key][1]}); }
            else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(1.2); }
            else if (event.key === '-') { event.preventDefault(); zoomBy(1 / 1.2); }
            else if (event.key === 'End') { event.preventDefault(); reset(); }
            else if (event.key === 'Home') { event.preventDefault(); overview(); }
        }
        function keyboardChange() { if (!keyboard.value) return; focus(keyboard.value); if (typeof callbacks.onSelect === 'function') callbacks.onSelect(keyboard.value); else setSelection(keyboard.value); }
        function resize() { if (cy && !destroyed) { cy.resize(); schedule(); } }
        miniButton.addEventListener('click', miniClick); miniButton.addEventListener('keydown', keydown); canvas.addEventListener('keydown', keydown); keyboard.addEventListener('change', keyboardChange);
        const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null; if (observer) observer.observe(root); global.addEventListener('resize', resize);
        function destroy() { if (destroyed) return; destroyed = true; if (frame) global.cancelAnimationFrame(frame); if (observer) observer.disconnect(); global.removeEventListener('resize', resize); miniButton.removeEventListener('click', miniClick); miniButton.removeEventListener('keydown', keydown); canvas.removeEventListener('keydown', keydown); keyboard.removeEventListener('change', keyboardChange); if (cy) { cy.destroy(); cy = null; } root.remove(); nodes.clear(); positions.clear(); }
        return {render: render, setSelection: setSelection, setHighlight: setHighlight, focus: focus, showNodes: showNodes, zoomBy: zoomBy, overview: overview, reset: reset, getLayoutInfo: getLayoutInfo, getViewportInfo: getViewportInfo, destroy: destroy};
    }
    global.GraphMap = {create: create};
}(window));
