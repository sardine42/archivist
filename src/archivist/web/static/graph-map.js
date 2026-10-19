(function (global) {
    'use strict';
    const TOP = 46, DAY = 86400000;
    const COLORS = {'generative-rec': '#527da9', 'discriminative-rec': '#b58252', llm: '#8d72aa', other: '#4e9083'};
    const text = function (value) { return value == null ? '' : String(value); };
    function el(tag, name, value) {
        const item = document.createElement(tag);
        if (name) item.className = name;
        if (value != null) item.textContent = value;
        return item;
    }
    function name(node) { return text(node.model_name || node.title || node.id); }
    function shorten(value, maximum) { const chars = Array.from(value); return chars.length > maximum ? chars.slice(0, maximum - 1).join('') + '…' : value; }
    function color(node) { return COLORS[(node.category || [])[0]] || COLORS.other; }
    function overlaps(a, b, gap) { return a.x < b.x + b.w + gap && a.x + a.w + gap > b.x && a.y < b.y + b.h + gap && a.y + a.h + gap > b.y; }

    function create(container, options) {
        if (!global.cytoscape || !global.GraphLayout) throw new Error('Graph renderer dependencies missing');
        const callbacks = options || {};
        const root = el('div', 'gm-map');
        const backdrop = el('div', 'gm-backdrop'), canvas = el('div', 'gm-canvas');
        const axis = el('div', 'gm-time-axis'), lanes = el('div', 'gm-lane-labels');
        const labelLayer = el('div', 'gm-node-labels'), tooltip = el('div', 'gm-tooltip');
        const notice = el('div', 'gm-low-zoom', '缩放查看论文名称');
        const mini = el('div', 'gm-mini'), miniButton = el('button', 'gm-mini-button'), miniCanvas = el('canvas', 'gm-mini-canvas');
        const keyboard = el('select', 'gm-keyboard-select');
        canvas.tabIndex = 0;
        canvas.setAttribute('role', 'application');
        canvas.setAttribute('aria-label', '论文时间地图，左右键平移，加减键缩放时间范围，End 定位最新。');
        backdrop.setAttribute('aria-hidden', 'true');
        axis.setAttribute('aria-hidden', 'true');
        lanes.setAttribute('aria-hidden', 'true');
        labelLayer.setAttribute('aria-label', '当前地图中的论文');
        tooltip.hidden = true;
        tooltip.setAttribute('role', 'tooltip');
        notice.hidden = true;
        miniButton.type = 'button';
        miniButton.setAttribute('aria-label', '全局小地图，点击定位，方向键平移，End 定位最新');
        miniButton.append(miniCanvas);
        mini.append(el('div', 'gm-mini-heading', '研究全景'), miniButton);
        keyboard.setAttribute('aria-label', '用键盘选择并定位论文节点');
        root.append(backdrop, canvas, axis, lanes, labelLayer, notice, mini, keyboard, tooltip);
        container.append(root);
        const measure = document.createElement('canvas').getContext('2d');
        measure.font = '500 12px -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif';
        let cy, layout, initialIds = [], nodes = new Map(), allRelations = [];
        let selected = '', highlighted = null, related = new Set(), similar = new Set();
        let destroyed = false, frame = 0, lastZoom = 0, miniTransform = null, labelCount = 0, asOf = 0;
        let lastWidth = 0, lastHeight = 0, focal = '', focalAlign = '', focalFit = false, focalRelated = false, projectionZoom = 0, projectionBase = 0;

        function gutter() { return canvas.clientWidth < 520 ? 88 : 118; }
        function reserveBottom() { return canvas.clientWidth < 520 ? 74 : 112; }
        function baselineZoom() {
            if (!layout) return 1;
            return Math.max(.35, Math.min(1.1, (canvas.clientHeight - TOP - reserveBottom()) / (layout.bounds.y2 - layout.bounds.y1)));
        }
        function topPan(zoom) {
            const available = canvas.clientHeight - TOP - reserveBottom();
            return TOP + Math.max(0, (available - (layout.bounds.y2 - layout.bounds.y1) * zoom) / 2) - layout.bounds.y1 * zoom;
        }
        function viewport(point, settings) {
            if (!cy || !point) return;
            const config = settings || {}, zoom = config.zoom || Math.max(baselineZoom(), cy.zoom());
            const x = config.align === 'right' ? canvas.clientWidth - 66 - point.x * zoom : gutter() + (canvas.clientWidth - gutter()) / 2 - point.x * zoom;
            const y = topPan(baselineZoom());
            cy.stop();
            const view = {zoom: zoom, pan: {x: x, y: y}};
            if (config.animate && !global.matchMedia('(prefers-reduced-motion: reduce)').matches) cy.animate(view, {duration: 240});
            else cy.viewport(view);
        }
        function nodeElement(node) {
            const point = layout.positions.get(node.id);
            return {group: 'nodes', data: {id: node.id, fill: color(node), reading: !!node.has_reading}, position: {x: point.x, y: point.y * (cy ? baselineZoom() / cy.zoom() : 1)}};
        }
        function schedule() {
            if (destroyed || !cy || frame) return;
            frame = global.requestAnimationFrame(function () { frame = 0; paint(); });
        }
        function updateNodeSizes(zoom) {
            if (Math.abs(zoom - lastZoom) < .005) return;
            lastZoom = zoom;
            const border = zoom < .25 ? .3 : 1.1;
            const size = Math.max(1, Math.min(zoom < .25 ? 3.5 : (zoom < .5 ? 5 : 7), layout.minDistance * zoom - 2 * border));
            cy.style().selector('node').style({width: size / zoom, height: size / zoom, 'border-width': border / zoom})
                .selector('node.gm-highlighted').style({width: (size + 1.5) / zoom, height: (size + 1.5) / zoom})
                .selector('node.gm-related').style({width: (size + 2) / zoom, height: (size + 2) / zoom})
                .selector('node.gm-selected').style({width: 13 / zoom, height: 13 / zoom, 'border-width': 3 / zoom})
                .selector('edge').style({width: 1.15 / zoom, 'arrow-scale': .75 / Math.pow(zoom, .3)})
                .update();
        }
        function projectVertical(zoom) {
            const base = baselineZoom();
            if (Math.abs(projectionZoom - zoom) < .001 && Math.abs(projectionBase - base) < .001) return;
            projectionZoom = zoom; projectionBase = base;
            cy.batch(function () {
                cy.nodes().forEach(function (item) {
                    const point = layout.positions.get(item.id());
                    item.position({x: point.x, y: point.y * base / zoom});
                });
            });
        }
        function visiblePoints() {
            const result = [];
            cy.nodes().forEach(function (element) {
                const point = element.renderedPosition();
                if (point.x >= gutter() - 12 && point.x <= canvas.clientWidth + 12 && point.y >= TOP - 8 && point.y <= canvas.clientHeight + 8) {
                    result.push({node: nodes.get(element.id()), x: point.x, y: point.y});
                }
            });
            return result;
        }
        function priority(node) {
            if (node.id === selected) return 100000;
            if (related.has(node.id)) return 50000;
            let score = highlighted && highlighted.has(node.id) ? 10000 : 0;
            if (!node.has_reading) score -= 5000;
            if (node.model_name !== node.title) score += 100;
            const stamp = Date.parse(node.published_date || '');
            if (Number.isFinite(stamp)) score += (stamp - (asOf - 365 * DAY)) / DAY;
            score += (Number(node.reading_score) || 0) * 12;
            return score;
        }
        function drawLabels(points) {
            labelLayer.replaceChildren();
            const placed = [], dots = points.filter(function (point) {
                if (selected) return point.node.id === selected || related.has(point.node.id) || similar.has(point.node.id);
                return !highlighted || highlighted.has(point.node.id);
            }).map(function (point) { return {id: point.node.id, x: point.x - 5, y: point.y - 5, w: 10, h: 10}; });
            let candidates = points.filter(function (point) {
                if (selected) return point.node.id === selected || related.has(point.node.id) || similar.has(point.node.id);
                if (highlighted) return highlighted.has(point.node.id);
                return point.node.has_reading;
            }).sort(function (a, b) { return priority(b.node) - priority(a.node) || a.node.id.localeCompare(b.node.id); });
            const maximum = Math.max(20, Math.min(90, Math.round((canvas.clientWidth - gutter()) * (canvas.clientHeight - TOP) / 11000)));
            if (cy.zoom() < .2 && !selected && !highlighted) candidates = [];
            const fragment = document.createDocumentFragment();
            candidates.some(function (point) {
                if (placed.length >= maximum && point.node.id !== selected) return true;
                const node = point.node, isSelected = node.id === selected;
                const label = shorten(name(node), isSelected ? 48 : 29);
                measure.font = (isSelected ? '650' : '500') + ' 12px -apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", sans-serif';
                const width = Math.min(isSelected ? 280 : 190, canvas.clientWidth - gutter() - 24, Math.ceil(measure.measureText(label).width) + (isSelected ? 18 : 8));
                const height = isSelected ? 26 : 20;
                const centeredX = Math.max(gutter() + 4, Math.min(canvas.clientWidth - width - 10, point.x - width / 2));
                const choices = [
                    {x: point.x + 9, y: point.y - height / 2, w: width, h: height},
                    {x: point.x - width - 9, y: point.y - height / 2, w: width, h: height},
                    {x: centeredX, y: point.y - height - 10, w: width, h: height},
                    {x: centeredX, y: point.y + 10, w: width, h: height},
                ];
                let box = choices.find(function (candidate) {
                    return candidate.x >= gutter() + 4 && candidate.x + candidate.w < canvas.clientWidth - 10 && candidate.y >= TOP + 4 && candidate.y + candidate.h < canvas.clientHeight - reserveBottom() + 10 &&
                        !placed.some(function (other) { return overlaps(candidate, other, 6); }) &&
                        !dots.some(function (other) { return other.id !== node.id && overlaps(candidate, other, 2); });
                });
                if (!box && isSelected) box = {x: Math.max(gutter() + 4, Math.min(canvas.clientWidth - width - 10, point.x + 10)), y: Math.max(TOP + 4, point.y - height - 10), w: width, h: height};
                if (!box) return false;
                const item = el('button', 'gm-node-label' + (isSelected ? ' gm-node-label--selected' : ''), label);
                item.type = 'button';
                item.dataset.node = node.id;
                item.style.left = box.x + 'px'; item.style.top = box.y + 'px'; item.style.width = box.w + 'px'; item.style.height = box.h + 'px';
                item.setAttribute('aria-label', name(node) + ' · ' + (node.published_date || '日期未知') + '，查看相关论文');
                fragment.append(item); placed.push(box);
                return false;
            });
            labelLayer.append(fragment);
            labelCount = placed.length;
        }
        function drawBackdrop(zoom, pan) {
            backdrop.replaceChildren(); lanes.replaceChildren(); axis.replaceChildren();
            const corner = el('span', 'gm-axis-corner', '研究方向'); axis.append(corner);
            const frontierX = layout.latestDate == null ? null : layout.xForDate(layout.latestDate) * zoom + pan.x;
            const frontierVisible = frontierX != null && frontierX > gutter() + 100 && frontierX < canvas.clientWidth + 25;
            const badgeX = frontierVisible ? Math.min(canvas.clientWidth - 88, frontierX - 62) : null;
            let lastTick = -1000;
            layout.ticks.forEach(function (tick) {
                const x = tick.x * zoom + pan.x;
                if (x < gutter() + 8 || x > canvas.clientWidth - 18 || x - lastTick < 68) return;
                const half = tick.label.length * 3.5;
                if (badgeX != null && x + half > badgeX - 8 && x - half < badgeX + 76) return;
                const item = el('span', 'gm-time-label', tick.label);
                item.style.left = x + 'px'; axis.append(item); lastTick = x;
                const line = el('div', 'gm-date-line'); line.style.left = x + 'px'; backdrop.append(line);
            });
            layout.lanes.forEach(function (lane) {
                const vertical = baselineZoom();
                const top = lane.top * vertical + pan.y, bottom = lane.bottom * vertical + pan.y, center = lane.center * vertical + pan.y;
                if (bottom < TOP || top > canvas.clientHeight) return;
                const guide = el('div', 'gm-lane-guide'); guide.style.top = center + 'px'; backdrop.append(guide);
                const label = el('div', 'gm-lane-label'); label.style.top = Math.max(TOP + 8, center - 9) + 'px';
                label.style.setProperty('--lane-color', lane.color || '#60758c');
                label.append(el('span', 'gm-lane-name', lane.label));
                lanes.append(label);
                if (highlighted && highlighted.size) {
                    const members = Array.from(highlighted).map(function (key) { return layout.positions.get(key); }).filter(function (point) { return point && point.laneId === lane.id; });
                    if (members.length) {
                        const x1 = Math.max(gutter(), Math.min(...members.map(function (point) { return point.x * zoom + pan.x; })) - 22);
                        const x2 = Math.min(canvas.clientWidth, Math.max(...members.map(function (point) { return point.x * zoom + pan.x; })) + 22);
                        const spot = el('div', 'gm-spotlight'); spot.style.left = x1 + 'px'; spot.style.top = Math.max(TOP, top + 5) + 'px';
                        spot.style.width = Math.max(0, x2 - x1) + 'px'; spot.style.height = Math.max(0, Math.min(canvas.clientHeight - 28, bottom - 5) - Math.max(TOP, top + 5)) + 'px';
                        backdrop.append(spot);
                    }
                }
            });
            if (frontierVisible) {
                const frontier = el('div', 'gm-frontier'); frontier.style.left = Math.max(gutter(), frontierX - 24) + 'px'; backdrop.append(frontier);
                const badge = el('span', 'gm-frontier-label', new Date(layout.latestDate).toISOString().slice(5, 10).replace('-', '.') + ' · 最新');
                badge.style.left = badgeX + 'px'; axis.append(badge);
            }
        }
        function drawMini() {
            const width = miniCanvas.clientWidth || 166, height = miniCanvas.clientHeight || 62, ratio = Math.min(2, global.devicePixelRatio || 1);
            miniCanvas.width = width * ratio; miniCanvas.height = height * ratio;
            const context = miniCanvas.getContext('2d');
            context.scale(ratio, ratio); context.clearRect(0, 0, width, height);
            const bounds = layout.bounds, scale = Math.min((width - 12) / (bounds.x2 - bounds.x1), (height - 8) / (bounds.y2 - bounds.y1));
            const left = (width - (bounds.x2 - bounds.x1) * scale) / 2, top = (height - (bounds.y2 - bounds.y1) * scale) / 2;
            miniTransform = {scale: scale, left: left, top: top};
            layout.positions.forEach(function (point, key) {
                const active = key === selected || (highlighted && highlighted.has(key));
                context.fillStyle = key === selected ? '#b23c28' : (active ? '#3b5f94' : (highlighted || selected ? '#d3d9df' : color(nodes.get(key))));
                const size = active ? 3 : 1.5;
                context.fillRect(left + (point.x - bounds.x1) * scale - size / 2, top + (point.y - bounds.y1) * scale - size / 2, size, size);
            });
            const extent = cy.extent();
            context.fillStyle = '#71859a12'; context.strokeStyle = '#7c8fa6'; context.lineWidth = 1;
            const rect = [left + (extent.x1 - bounds.x1) * scale, top, Math.max(3, extent.w * scale), Math.max(4, (bounds.y2 - bounds.y1) * scale)];
            context.fillRect(...rect); context.strokeRect(...rect);
        }
        function paint() {
            if (!cy || destroyed) return;
            const zoom = cy.zoom(), pan = cy.pan();
            root.style.setProperty('--gm-gutter', gutter() + 'px');
            projectVertical(zoom); updateNodeSizes(zoom); drawBackdrop(zoom, pan); drawLabels(visiblePoints()); drawMini();
            notice.hidden = zoom >= .25;
            if (callbacks.onViewportChange) callbacks.onViewportChange(getViewportInfo());
        }
        function emphasize() {
            if (!cy) return;
            cy.batch(function () {
                cy.nodes().forEach(function (item) {
                    const key = item.id();
                    item.toggleClass('gm-selected', key === selected);
                    item.toggleClass('gm-related', related.has(key));
                    item.toggleClass('gm-similar', similar.has(key));
                    item.toggleClass('gm-highlighted', !!highlighted && highlighted.has(key));
                    item.toggleClass('gm-dimmed', selected ? key !== selected && !related.has(key) && !similar.has(key) : !!highlighted && !highlighted.has(key));
                });
            });
            keyboard.value = selected; lastZoom = 0; schedule();
        }
        function setSelection(value, settings) {
            if (!cy) return;
            const config = settings || {}; selected = value || '';
            related = new Set(config.relatedIds || []); similar = new Set(config.similarIds || []);
            const seen = new Set();
            cy.batch(function () {
                cy.edges().remove();
                (config.relations || allRelations).forEach(function (relation, index) {
                    const key = relation.source + '\0' + relation.target + '\0' + relation.type;
                    if (!selected || (relation.source !== selected && relation.target !== selected) || !nodes.has(relation.source) || !nodes.has(relation.target) || seen.has(key)) return;
                    seen.add(key);
                    cy.add({group: 'edges', data: {id: '__relation_' + index, source: relation.source, target: relation.target, type: relation.type}});
                });
            });
            emphasize();
        }
        function setHighlight(values) { highlighted = values == null ? null : new Set(values); emphasize(); }
        function syncKeyboard() {
            keyboard.replaceChildren();
            const first = el('option', '', '选择论文节点…'); first.value = ''; keyboard.append(first);
            Array.from(nodes.values()).sort(function (a, b) { return text(b.published_date).localeCompare(text(a.published_date)); }).forEach(function (node) {
                const option = el('option', '', (node.published_date || '日期未知') + ' · ' + name(node)); option.value = node.id; keyboard.append(option);
            });
            keyboard.value = selected;
        }
        function showNodes(values) {
            if (!cy) return;
            const additions = values.filter(function (node) { return !nodes.has(node.id); });
            if (!additions.length) return;
            additions.forEach(function (node) { nodes.set(node.id, node); });
            layout.add(additions);
            cy.add(additions.map(nodeElement)); syncKeyboard(); emphasize();
        }
        function reset() {
            if (!cy) return;
            const latest = initialIds.map(function (key) { return layout.positions.get(key); }).filter(Boolean).sort(function (a, b) { return (b.date || 0) - (a.date || 0); })[0];
            focal = ''; focalAlign = ''; focalFit = false; focalRelated = false;
            viewport(latest, {align: 'right', zoom: baselineZoom()});
        }
        function focus(key, settings) {
            if (!layout || !layout.positions.has(key)) return;
            focal = key; focalAlign = settings && settings.align || ''; focalFit = !!(settings && settings.fitHighlight); focalRelated = !!(settings && settings.fitRelated);
            const config = Object.assign({animate: true}, settings || {});
            if (config.fitHighlight && highlighted && highlighted.size) {
                const points = Array.from(highlighted).map(function (id) { return layout.positions.get(id); }).filter(function (point) { return point && point.date != null; });
                if (points.length > 1) {
                    const xs = points.map(function (point) { return point.x; });
                    config.zoom = Math.max(.02, Math.min(baselineZoom(), (canvas.clientWidth - gutter() - 120) / (Math.max(...xs) - Math.min(...xs) + 30)));
                }
            }
            let anchor = layout.positions.get(key);
            if (config.fitRelated && related.size) {
                const points = [key].concat(Array.from(related)).map(function (id) { return layout.positions.get(id); }).filter(function (point) { return point && point.date != null; });
                if (points.length > 1) {
                    const xs = points.map(function (point) { return point.x; });
                    const left = Math.min(...xs), right = Math.max(...xs);
                    config.zoom = Math.max(.02, Math.min(baselineZoom(), (canvas.clientWidth - gutter() - 100) / (right - left + 30)));
                    anchor = {x: (left + right) / 2, y: anchor.y};
                }
            }
            viewport(anchor, config);
        }
        function zoomBy(factor) {
            if (!cy) return;
            cy.zoom({level: Math.max(.02, Math.min(2.4, cy.zoom() * factor)), renderedPosition: {x: gutter() + (canvas.clientWidth - gutter()) / 2, y: (canvas.clientHeight + TOP) / 2}});
        }
        function overview() {
            if (!cy) return;
            const bounds = layout.bounds;
            const zoom = Math.max(.02, Math.min((canvas.clientWidth - gutter() - 45) / (bounds.x2 - bounds.x1), baselineZoom()));
            cy.stop(); cy.viewport({zoom: zoom, pan: {x: gutter() + 20 - bounds.x1 * zoom, y: topPan(baselineZoom())}});
        }
        function showTooltip(key, point) {
            const node = nodes.get(key); if (!node) return;
            tooltip.replaceChildren(el('strong', '', name(node)), el('span', '', node.title === name(node) ? '' : node.title), el('time', '', node.published_date || '日期未知 · 仅引用'));
            tooltip.style.left = Math.max(gutter(), Math.min(canvas.clientWidth - tooltip.clientWidth - 10, point.x + 14)) + 'px';
            tooltip.style.top = Math.max(TOP + 4, Math.min(canvas.clientHeight - 140, point.y + 18)) + 'px';
            tooltip.hidden = false;
        }
        function render(payload) {
            if (cy) { cy.destroy(); cy = null; }
            projectionZoom = 0; projectionBase = 0; lastZoom = 0;
            nodes = new Map((payload.nodes || []).map(function (node) { return [node.id, node]; }));
            initialIds = Array.from(nodes.keys()); allRelations = payload.relations || [];
            selected = ''; related = new Set(); similar = new Set(); highlighted = null; asOf = Date.parse(payload.asOf || payload.as_of || '');
            layout = global.GraphLayout.create({nodes: Array.from(nodes.values()), lineages: payload.lineages || [], asOf: payload.asOf || payload.as_of});
            cy = global.cytoscape({container: canvas, elements: Array.from(nodes.values()).map(nodeElement), layout: {name: 'preset', fit: false},
                minZoom: .02, maxZoom: 2.4, boxSelectionEnabled: false, autoungrabify: true, wheelSensitivity: .22, pixelRatio: Math.min(2, global.devicePixelRatio || 1),
                style: [
                    {selector: 'node', style: {shape: 'ellipse', width: 8, height: 8, 'background-color': 'data(fill)', 'border-color': '#fff', 'border-width': 1.1, 'overlay-opacity': 0, 'z-index': 10}},
                    {selector: 'node.gm-dimmed', style: {opacity: .16}},
                    {selector: 'node.gm-highlighted', style: {'border-color': '#fff', 'z-index': 20}},
                    {selector: 'node.gm-related', style: {'border-color': '#fff', 'z-index': 25}},
                    {selector: 'node.gm-similar', style: {'border-color': '#fff'}},
                    {selector: 'node.gm-selected', style: {'background-color': '#b23c28', 'border-color': '#f5ddd4', 'z-index': 30}},
                    {selector: 'edge', style: {width: 1.25, 'curve-style': 'bezier', 'control-point-step-size': 46, 'target-arrow-shape': 'triangle', 'arrow-scale': .7, opacity: .6, 'overlay-opacity': 0, 'z-index': 1}},
                    {selector: 'edge[type="citation"]', style: {'line-color': '#9aa9b6', 'target-arrow-color': '#9aa9b6', 'line-style': 'dashed', 'line-dash-pattern': [4, 5]}},
                    {selector: 'edge[type="comparison"]', style: {'line-color': '#728ca1', 'target-arrow-color': '#728ca1'}},
                ]});
            cy.on('pan zoom resize', function () {
                tooltip.hidden = true;
                const fixed = topPan(baselineZoom());
                if (Math.abs(cy.pan().y - fixed) > .2) cy.pan('y', fixed);
                schedule();
            });
            cy.on('tap', 'node', function (event) { if (callbacks.onSelect) callbacks.onSelect(event.target.id()); });
            cy.on('mouseover', 'node', function (event) { showTooltip(event.target.id(), event.target.renderedPosition()); });
            cy.on('mouseout', 'node', function () { tooltip.hidden = true; });
            lastWidth = canvas.clientWidth; lastHeight = canvas.clientHeight;
            syncKeyboard(); reset(); schedule();
        }
        function getLayoutInfo() { return {nodeCount: nodes.size, lanes: layout ? layout.lanes : [], latestDate: layout ? layout.latestDate : '', bounds: layout ? layout.bounds : {}}; }
        function getViewportInfo() { return {zoom: cy ? cy.zoom() : 1, nodeCount: nodes.size, visibleNodes: cy ? visiblePoints().length : 0, labelCount: labelCount, selectedId: selected}; }
        function clickLabel(event) {
            const button = event.target.closest('button[data-node]');
            if (button && callbacks.onSelect) callbacks.onSelect(button.dataset.node);
        }
        function hoverLabel(event) {
            const button = event.target.closest('button[data-node]');
            if (button && cy) showTooltip(button.dataset.node, cy.getElementById(button.dataset.node).renderedPosition());
        }
        function miniClick(event) {
            if (!cy || !miniTransform) return;
            if (event.detail === 0) { overview(); return; }
            const rect = miniCanvas.getBoundingClientRect(), bounds = layout.bounds;
            viewport({x: (event.clientX - rect.left - miniTransform.left) / miniTransform.scale + bounds.x1, y: (event.clientY - rect.top - miniTransform.top) / miniTransform.scale + bounds.y1}, {});
        }
        function keydown(event) {
            if (!cy || event.altKey || event.ctrlKey || event.metaKey) return;
            const movement = {ArrowLeft: [80, 0], ArrowRight: [-80, 0]};
            if (movement[event.key]) { event.preventDefault(); cy.panBy({x: movement[event.key][0], y: movement[event.key][1]}); }
            else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(1.25); }
            else if (event.key === '-') { event.preventDefault(); zoomBy(.8); }
            else if (event.key === 'End') { event.preventDefault(); reset(); }
            else if (event.key === 'Home') { event.preventDefault(); overview(); }
        }
        function keyboardChange() { if (keyboard.value && callbacks.onSelect) callbacks.onSelect(keyboard.value); }
        function resize() {
            if (!cy || destroyed) return;
            const changed = Math.abs(lastWidth - canvas.clientWidth) > 1 || Math.abs(lastHeight - canvas.clientHeight) > 1;
            lastWidth = canvas.clientWidth; lastHeight = canvas.clientHeight;
            cy.resize();
            if (changed) {
                if (focal && layout.positions.has(focal)) focus(focal, {align: focalAlign, fitHighlight: focalFit, fitRelated: focalRelated, animate: false, zoom: baselineZoom()});
                else reset();
            }
            schedule();
        }
        labelLayer.addEventListener('click', clickLabel); labelLayer.addEventListener('mouseover', hoverLabel);
        labelLayer.addEventListener('mouseleave', function () { tooltip.hidden = true; });
        miniButton.addEventListener('click', miniClick); miniButton.addEventListener('keydown', keydown); canvas.addEventListener('keydown', keydown); keyboard.addEventListener('change', keyboardChange);
        const observer = new ResizeObserver(resize); observer.observe(root);
        function destroy() { destroyed = true; if (frame) global.cancelAnimationFrame(frame); observer.disconnect(); if (cy) cy.destroy(); root.remove(); }
        return {render: render, showNodes: showNodes, setSelection: setSelection, setHighlight: setHighlight, focus: focus, reset: reset, zoomBy: zoomBy, overview: overview, getLayoutInfo: getLayoutInfo, getViewportInfo: getViewportInfo, destroy: destroy};
    }
    global.GraphMap = {create: create};
})(window);
