(function (global) {
    'use strict';

    const NS = 'http://www.w3.org/2000/svg';
    const COLORS = {'generative-rec': '#3165a1', 'discriminative-rec': '#95521e', llm: '#7851a3', other: '#246e65'};
    const AXIS = 29, PADDING = 52;
    const clamp = function (value, low, high) { return Math.max(low, Math.min(high, value)); };
    const label = function (node) { return String(node.model_name || node.name || node.title || node.id); };
    function displayLabel(node, compact) {
        const value = label(node), model = String(node.model_name || node.name || '').trim();
        if (model && model !== String(node.title || '').trim()) return value;
        const characters = Array.from(value);
        const limit = compact ? 32 : 54;
        if (characters.length <= limit) return value;
        const shortened = characters.slice(0, limit - 2).join('');
        const boundary = shortened.lastIndexOf(' ');
        return (boundary > limit * .65 ? shortened.slice(0, boundary) : shortened) + '…';
    }
    const svg = function (tag, attrs) {
        const item = document.createElementNS(NS, tag);
        Object.keys(attrs || {}).forEach(function (key) { item.setAttribute(key, attrs[key]); });
        return item;
    };
    const html = function (tag, className) {
        const item = document.createElement(tag);
        if (className) item.className = className;
        return item;
    };
    function overlap(a, b, gap) {
        return a.x < b.x + b.w + gap && a.x + a.w + gap > b.x && a.y < b.y + b.h + gap && a.y + a.h + gap > b.y;
    }
    function grid(cell) {
        const cells = new Map();
        function keys(box) {
            const result = [];
            for (let x = Math.floor(box.x / cell); x <= Math.floor((box.x + box.w) / cell); x++) {
                for (let y = Math.floor(box.y / cell); y <= Math.floor((box.y + box.h) / cell); y++) result.push(x + ':' + y);
            }
            return result;
        }
        return {
            add: function (box) { keys(box).forEach(function (key) { if (!cells.has(key)) cells.set(key, []); cells.get(key).push(box); }); },
            collides: function (box, gap, excluded) {
                const area = {x: box.x - gap, y: box.y - gap, w: box.w + gap * 2, h: box.h + gap * 2};
                return keys(area).some(function (key) {
                    return (cells.get(key) || []).some(function (other) { return (excluded == null || other.id !== excluded) && overlap(box, other, gap); });
                });
            }
        };
    }

    function create(container, callbacks) {
        callbacks = callbacks || {};
        const root = html('div', 'rm-map'), scene = svg('svg', {'class': 'rm-scene', 'aria-hidden': 'true'});
        const edgeLayer = svg('g', {'class': 'rm-edges'}), leaderLayer = svg('g', {'class': 'rm-name-leaders'});
        const nodeLayer = html('div', 'rm-nodes'), axis = html('div', 'rm-time-axis');
        root.tabIndex = 0;
        root.setAttribute('role', 'application');
        root.setAttribute('aria-label', '论文关联图');
        nodeLayer.setAttribute('aria-label', '论文节点');
        axis.setAttribute('aria-hidden', 'true');
        scene.append(edgeLayer, leaderLayer);
        root.append(scene, nodeLayer, axis);
        container.append(root);
        const measure = document.createElement('canvas').getContext('2d');
        let network = {nodes: [], links: []}, nodes = new Map(), links = new Map(), pointById = new Map();
        let selected = null, selectedLink = null, highlighted = null, filtered = null, focused = null, related = new Set(), localLinks = new Map();
        let width = 0, height = 0, worldWidth = 2400, worldHeight = 1400, baseZoom = 1;
        let camera = {zoom: 1, x: 0, y: 0}, frame = 0, labelTimer = 0, destroyed = false, pointer = null;
        let initial = true, filterChanged = false, nodeViews = new Map(), edgeViews = [], dates = [], datePositions = [], tickDates = [];
        let maxCitations = 1, emphasisThreshold = 10, majorThreshold = 34;

        function citationCount(node) {
            const value = Number(node && node.citation_count);
            return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
        }
        function citationRadius(node) {
            return 7 + 9 * Math.log1p(Math.min(maxCitations, citationCount(node))) / Math.log1p(maxCitations);
        }
        function citationTier(node) {
            const count = citationCount(node);
            return count >= majorThreshold ? 2 : count >= emphasisThreshold ? 1 : 0;
        }

        function nodeAllowed(id) { return nodes.has(id) && (!filtered || filtered.has(id)); }
        function linkAllowed(link) { return link && nodeAllowed(link.source) && nodeAllowed(link.target); }
        function schedule(labels) {
            if (destroyed) return;
            if (labels) root.dataset.layoutNames = 'true';
            if (frame) return;
            frame = global.requestAnimationFrame(function () {
                frame = 0;
                draw();
                if (root.dataset.layoutNames === 'true') {
                    delete root.dataset.layoutNames;
                    placeNames();
                }
            });
        }
        function emitViewport() {
            if (callbacks.onViewportChange) callbacks.onViewportChange({zoom: camera.zoom / baseZoom});
        }
        function viewportChanged(deferLabels) {
            schedule(!deferLabels);
            if (deferLabels) {
                leaderLayer.style.opacity = '0';
                global.clearTimeout(labelTimer);
                labelTimer = global.setTimeout(function () { schedule(true); }, 90);
            }
            emitViewport();
        }
        function fit() {
            if (filtered) {
                const scope = Array.from(pointById.values()).filter(function (point) { return nodeAllowed(point.id); });
                if (scope.length) {
                    const left = Math.min.apply(null, scope.map(function (point) { return point.x; }));
                    const right = Math.max.apply(null, scope.map(function (point) { return point.x; }));
                    const labelPadding = PADDING + 70;
                    const zoom = Math.min(baseZoom, Math.max(1, width - labelPadding * 2) / Math.max(1, right - left));
                    camera = {zoom: zoom, x: width / 2 - (left + right) / 2 * zoom, y: (height - AXIS - worldHeight * zoom) / 2};
                    viewportChanged(false);
                    return;
                }
            }
            const zoom = overviewZoom();
            camera = {zoom: zoom, x: (width - worldWidth * zoom) / 2, y: (height - AXIS - worldHeight * zoom) / 2};
            viewportChanged(false);
        }
        function overviewZoom() {
            return Math.max(.05, Math.min((width - PADDING * 2) / worldWidth, (height - AXIS - PADDING * 2) / worldHeight));
        }
        function startView() {
            if (filtered) { fit(); return; }
            if (network.nodes.length <= 90) { fit(); return; }
            let right = 0;
            pointById.forEach(function (point) { right = Math.max(right, point.x); });
            camera = {zoom: baseZoom, x: width - 84 - right * baseZoom, y: (height - AXIS - worldHeight * baseZoom) / 2};
            viewportChanged(false);
        }
        function project(point) {
            const vertical = Math.max(camera.zoom, baseZoom);
            return {x: point.x * camera.zoom + camera.x, y: (point.y - worldHeight / 2) * vertical + worldHeight / 2 * camera.zoom + camera.y};
        }
        function labelSize(value, size, weight) {
            measure.font = weight + ' ' + size + 'px -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif';
            const maximum = 190, raw = measure.measureText(value).width;
            if (raw <= maximum) return {w: Math.ceil(raw) + 6, h: Math.ceil(size * 1.3) + 2};
            let row = '', rowWidth = 0, lines = 1;
            const parts = value.match(/\S+\s*|\s+/g) || [value];
            parts.forEach(function (word) {
                let wordWidth = measure.measureText(word).width;
                if (wordWidth > maximum) {
                    Array.from(word).forEach(function (letter) {
                        const next = measure.measureText(letter).width;
                        if (rowWidth + next > maximum) { lines++; row = ''; rowWidth = 0; }
                        row += letter; rowWidth += next;
                    });
                } else {
                    if (rowWidth + wordWidth > maximum) { lines++; row = ''; rowWidth = 0; }
                    row += word; rowWidth += wordWidth;
                }
            });
            return {w: maximum + 6, h: Math.ceil(lines * size * 1.3) + 2};
        }
        function buildPositions() {
            const count = network.nodes.length;
            worldWidth = Math.max(2400, count * 28);
            baseZoom = Math.max(.1, Math.min(1.2, (width - PADDING * 2) / Math.min(worldWidth, 1920)));
            const usableHeight = Math.max(300, height - AXIS - PADDING * 2);
            worldHeight = Math.max(600, usableHeight / baseZoom);
            const minDistance = 28 / baseZoom, overview = overviewZoom();
            const occupied = grid(minDistance), overviewOccupied = grid(28 / overview);
            pointById = new Map();
            network.nodes.slice().sort(function (a, b) { return citationCount(b) - citationCount(a) || a.x - b.x || a.y - b.y || a.id.localeCompare(b.id); }).forEach(function (node) {
                const x = clamp(Number(node.x) || 0, 0, 1) * (worldWidth - 100) + 50;
                const original = clamp(Number(node.y) || 0, 0, 1) * (worldHeight - 100) + 50;
                const radius = citationRadius(node), half = (radius + 6) / baseZoom;
                const overviewHalfX = (radius * .5 + 4) / overview, overviewHalfY = (radius * .5 + 4) / baseZoom;
                const padding = Math.max(half, overviewHalfY);
                let y = original, found = false;
                for (let distance = 0; distance < worldHeight; distance += minDistance / 5) {
                    for (const sign of [1, -1]) {
                        const candidate = original + distance * sign;
                        if (candidate < padding || candidate > worldHeight - padding) continue;
                        const box = {id: node.id, x: x - half, y: candidate - half, w: half * 2, h: half * 2};
                        const overviewBox = {id: node.id, x: x - overviewHalfX, y: candidate - overviewHalfY, w: overviewHalfX * 2, h: overviewHalfY * 2};
                        if (!occupied.collides(box, 0) && !overviewOccupied.collides(overviewBox, 0)) { y = candidate; found = true; break; }
                    }
                    if (found) break;
                }
                const point = {id: node.id, x: x, y: y};
                pointById.set(node.id, point);
                occupied.add({id: node.id, x: x - half, y: y - half, w: half * 2, h: half * 2});
                overviewOccupied.add({id: node.id, x: x - overviewHalfX, y: y - overviewHalfY, w: overviewHalfX * 2, h: overviewHalfY * 2});
            });
        }
        function decorate(button, node) {
            const radius = citationRadius(node), image = svg('svg', {viewBox: '0 0 48 48', 'aria-hidden': 'true'});
            image.append(svg('circle', {cx: 24, cy: 24, r: radius, 'class': 'rm-node-background'}));
            let categories = Array.from(new Set(Array.isArray(node.category) ? node.category : [node.category])).filter(function (type) { return COLORS[type]; });
            if (!categories.length) categories = ['other'];
            const circumference = 2 * Math.PI * radius;
            categories.forEach(function (type, index) {
                const attrs = {cx: 24, cy: 24, r: radius, stroke: COLORS[type], 'class': 'rm-type-ring', 'data-category': type};
                if (categories.length > 1) {
                    attrs['stroke-dasharray'] = (circumference / categories.length - 1.3) + ' ' + circumference;
                    attrs['stroke-dashoffset'] = -index * circumference / categories.length;
                    attrs.transform = 'rotate(-90 24 24)';
                }
                image.append(svg('circle', attrs));
            });
            image.append(svg('circle', {cx: 24, cy: 24, r: radius * .43, 'class': 'rm-node-core'}));
            image.append(svg('circle', {cx: 24, cy: 24, r: radius + 4.2, 'class': 'rm-node-outline'}));
            button.append(image);
        }
        function build() {
            nodeLayer.replaceChildren(); edgeLayer.replaceChildren(); leaderLayer.replaceChildren();
            nodeViews = new Map(); edgeViews = [];
            network.nodes.forEach(function (node, index) {
                const button = html('button', 'rm-node'), name = html('span', 'rm-node-name');
                button.type = 'button'; button.dataset.rmNode = node.id; button.tabIndex = index ? -1 : 0;
                const count = citationCount(node), cited = '图谱内被引 ' + count + ' 篇';
                button.dataset.citations = String(count);
                button.setAttribute('aria-label', label(node) + '，' + cited); button.setAttribute('aria-pressed', 'false');
                button.title = (label(node) === node.title ? label(node) : label(node) + '\n' + (node.title || '')) + '\n' + cited;
                decorate(button, node); name.textContent = displayLabel(node); button.append(name); nodeLayer.append(button);
                nodeViews.set(node.id, {button: button, name: name, point: null, label: null});
            });
            network.links.forEach(function (link) {
                if (!pointById.has(link.source) || !pointById.has(link.target)) return;
                const group = svg('g', {'data-rm-link': link.id}), line = svg('path', {'class': 'rm-link'}), hit = svg('path', {'class': 'rm-link-hit'});
                const title = svg('title'); title.textContent = label(nodes.get(link.source)) + ' ↔ ' + label(nodes.get(link.target));
                hit.append(title); group.append(line, hit); edgeLayer.append(group);
                edgeViews.push({link: link, group: group, line: line, hit: hit});
            });
        }
        function connectionVisible(link) {
            if (!linkAllowed(link)) return false;
            if (selectedLink) return link.id === selectedLink;
            if (selected) return (link.source === selected || link.target === selected) && (!!link.display || (link.types || []).length || (link.relation_ids || []).length || (localLinks.get(selected) || new Set()).has(link.id));
            if (highlighted) return !!link.display && highlighted.has(link.source) && highlighted.has(link.target);
            return !!link.display;
        }
        function updateRelated() {
            related = new Set(nodeAllowed(selected) ? [selected] : []);
            if (selectedLink && linkAllowed(links.get(selectedLink))) {
                const link = links.get(selectedLink); related.add(link.source); related.add(link.target);
            }
            if (selected) network.links.forEach(function (link) {
                if (!connectionVisible(link)) return;
                if (link.source === selected) related.add(link.target);
                if (link.target === selected) related.add(link.source);
            });
        }
        function paint() {
            updateRelated();
            nodeViews.forEach(function (view, id) {
                if (!nodeAllowed(id)) view.button.hidden = true;
                const lit = highlighted || selected || selectedLink ? !!highlighted && highlighted.has(id) || related.has(id) : true;
                view.button.classList.toggle('rm-dim', !lit);
                view.button.classList.toggle('rm-highlight', !!highlighted && highlighted.has(id));
                view.button.classList.toggle('rm-selected', id === selected);
                view.button.setAttribute('aria-pressed', String(id === selected));
            });
            leaderLayer.querySelectorAll('[data-rm-node-leader]').forEach(function (leader) {
                const id = leader.getAttribute('data-rm-node-leader'), view = nodeViews.get(id);
                leader.style.display = nodeAllowed(id) ? '' : 'none';
                leader.setAttribute('stroke-opacity', view && view.button.classList.contains('rm-dim') ? '.08' : '.35');
            });
            edgeViews.forEach(function (view) {
                const active = connectionVisible(view.link), faint = linkAllowed(view.link) && !highlighted && !selectedLink && selected && view.link.display;
                const a = nodeViews.get(view.link.source).point, b = nodeViews.get(view.link.target).point;
                const inside = function (point) { return point && point.x >= -14 && point.x <= width + 14 && point.y >= -14 && point.y <= height - AXIS + 14; };
                const inFrame = selected || selectedLink || inside(a) && inside(b);
                view.group.style.display = (active || faint) && inFrame ? '' : 'none';
                view.hit.style.pointerEvents = active && inFrame ? 'stroke' : 'none';
                view.line.setAttribute('stroke-opacity', active ? selected || selectedLink || highlighted ? '.72' : '.24' : '.065');
                view.line.setAttribute('stroke-width', active && (selected || selectedLink || highlighted) ? '1.45' : '1.05');
            });
        }
        function edgePath(a, b) {
            if (a.x > b.x || a.x === b.x && a.y > b.y) { const swap = a; a = b; b = swap; }
            const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
            const sourceRadius = Math.min(a.radius + 2 * nodeScale(), length / 2), targetRadius = Math.min(b.radius + 2 * nodeScale(), length / 2);
            const ax = a.x + dx / length * sourceRadius, ay = a.y + dy / length * sourceRadius;
            const bx = b.x - dx / length * targetRadius, by = b.y - dy / length * targetRadius;
            const bend = Math.min(110, Math.abs(dx) * .36);
            return 'M' + ax + ',' + ay + 'C' + (ax + bend) + ',' + ay + ' ' + (bx - bend) + ',' + by + ' ' + bx + ',' + by;
        }
        function draw() {
            if (!width || !height) return;
            scene.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
            nodeViews.forEach(function (view, id) {
                const point = project(pointById.get(id)); view.point = point;
                const scale = nodeScale(), size = 48 * scale, image = view.button.querySelector('svg');
                point.radius = citationRadius(nodes.get(id)) * scale;
                const buttonSize = Math.max(24, 2 * (point.radius + 5.2 * scale));
                view.center = buttonSize / 2;
                view.button.style.width = buttonSize + 'px'; view.button.style.height = buttonSize + 'px';
                view.button.style.marginLeft = -view.center + 'px'; view.button.style.marginTop = -view.center + 'px';
                image.style.width = size + 'px'; image.style.height = size + 'px';
                image.style.left = (buttonSize - size) / 2 + 'px'; image.style.top = (buttonSize - size) / 2 + 'px';
                const visible = nodeAllowed(id) && point.x > -220 && point.x < width + 220 && point.y > -140 && point.y < height - AXIS + 140;
                view.button.hidden = !visible;
                if (visible) {
                    view.button.style.left = point.x + 'px'; view.button.style.top = point.y + 'px';
                    if (view.label) {
                        const labelBox = view.label;
                        view.name.style.left = (view.center + labelBox.dx - labelBox.w / 2) + 'px';
                        view.name.style.top = (view.center + labelBox.dy - labelBox.h / 2) + 'px';
                    }
                }
            });
            edgeViews.forEach(function (view) {
                const a = nodeViews.get(view.link.source).point, b = nodeViews.get(view.link.target).point, path = edgePath(a, b);
                view.line.setAttribute('d', path); view.hit.setAttribute('d', path);
            });
            drawAxis();
            paint();
            if (filtered || filterChanged) { updateRovingFocus(); filterChanged = false; }
        }
        function readableScope() { return filtered && filtered.size <= 90; }
        function nodeScale() { return readableScope() ? 1 : clamp(Math.sqrt(camera.zoom / baseZoom), .5, 34 / 28); }
        function placeNames() {
            leaderLayer.replaceChildren();
            leaderLayer.style.opacity = '';
            const occupied = grid(48), circles = grid(48), available = [];
            const relative = camera.zoom / baseZoom, compact = !readableScope() && relative < .65;
            const baseFontSize = readableScope() ? 12 : relative > 1.25 ? 13 : compact ? 9 : 12;
            const labelGap = compact ? 2 : 3;
            nodeViews.forEach(function (view, id) {
                if (view.button.hidden) return;
                const point = view.point;
                view.name.hidden = point.x < 9 || point.x > width - 9 || point.y < 9 || point.y > height - AXIS - 9;
                if (view.name.hidden) return;
                const node = nodes.get(id), radius = point.radius + 3 * nodeScale(), tier = citationTier(node);
                const fontSize = baseFontSize + (tier === 2 ? 3 : tier === 1 ? 1 : 0), weight = tier === 2 ? 700 : tier === 1 ? 650 : 450;
                circles.add({id: id, x: point.x - radius, y: point.y - radius, w: radius * 2, h: radius * 2});
                view.name.textContent = displayLabel(node, compact);
                view.name.style.fontSize = fontSize + 'px';
                view.name.style.setProperty('--rm-label-weight', String(weight));
                view.name.style.setProperty('--rm-selected-label-weight', String(Math.max(650, weight)));
                const size = labelSize(displayLabel(node, compact), fontSize, Math.max(650, weight));
                view.name.style.width = size.w + 'px';
                available.push({id: id, view: view, point: point, size: size, citations: citationCount(node), radius: radius});
            });
            // Citation counts belong to the full graph, so filtering never changes label priority.
            available.sort(function (a, b) { return b.citations - a.citations || b.point.x - a.point.x || a.point.y - b.point.y || a.id.localeCompare(b.id); });
            available.forEach(function (item) {
                const p = item.point, size = item.size, w = size.w, h = size.h, clearance = item.radius + 5;
                const offsets = [[0, clearance + h / 2], [0, -clearance - h / 2], [clearance + w / 2, 0], [-clearance - w / 2, 0], [w / 2 + clearance, clearance + h / 2], [-w / 2 - clearance, -clearance - h / 2]];
                for (let radius = Math.max(36, clearance + 12); radius <= 228; radius += 12) {
                    for (let step = 0; step < 16; step++) {
                        const angle = Math.PI / 8 * step;
                        offsets.push([Math.cos(angle) * (radius + w * .18), Math.sin(angle) * (radius + h * .1)]);
                    }
                }
                let chosen = null, fallback = null, lowestCost = Infinity;
                offsets.some(function (offset) {
                    const box = {x: p.x + offset[0] - w / 2, y: p.y + offset[1] - h / 2, w: w, h: h};
                    if (box.x < 3 || box.x + w > width - 3 || box.y < 3 || box.y + h > height - AXIS - 4) return false;
                    const labelCollision = occupied.collides(box, labelGap), nodeCollision = circles.collides(box, 2);
                    if (!labelCollision && !nodeCollision) { chosen = {dx: offset[0], dy: offset[1], w: w, h: h, box: box}; return true; }
                    const cost = (labelCollision ? 1000 : 0) + (nodeCollision ? 500 : 0) + Math.hypot(offset[0], offset[1]);
                    if (cost < lowestCost) { lowestCost = cost; fallback = {dx: offset[0], dy: offset[1], w: w, h: h, box: box}; }
                    return false;
                });
                if (!chosen) {
                    let nearest = Infinity;
                    for (let y = 4; y + h < height - AXIS - 4; y += 4) {
                        for (let x = 4; x + w < width - 4; x += 6) {
                            const dx = x + w / 2 - p.x, dy = y + h / 2 - p.y, distance = dx * dx + dy * dy;
                            if (distance >= nearest) continue;
                            const box = {x: x, y: y, w: w, h: h};
                            if (!occupied.collides(box, labelGap) && !circles.collides(box, 2)) {
                                nearest = distance; chosen = {dx: dx, dy: dy, w: w, h: h, box: box};
                            }
                        }
                    }
                }
                if (!chosen) chosen = fallback || {dx: 0, dy: clearance + h / 2, w: w, h: h, box: {x: p.x - w / 2, y: p.y + clearance, w: w, h: h}};
                item.view.label = chosen;
                item.view.name.style.left = (item.view.center + chosen.dx - w / 2) + 'px';
                item.view.name.style.top = (item.view.center + chosen.dy - h / 2) + 'px';
                occupied.add(chosen.box);
                if (Math.hypot(chosen.dx, chosen.dy) > Math.max(34, h / 2 + clearance + 5)) {
                    const endX = clamp(p.x, chosen.box.x, chosen.box.x + w), endY = clamp(p.y, chosen.box.y, chosen.box.y + h);
                    const dx = endX - p.x, dy = endY - p.y, distance = Math.hypot(dx, dy) || 1, start = p.radius + 2 * nodeScale();
                    const leader = svg('path', {d: 'M' + (p.x + dx / distance * start) + ',' + (p.y + dy / distance * start) + 'L' + endX + ',' + endY, 'class': 'rm-name-leader', 'data-rm-node-leader': item.id});
                    if (item.view.button.classList.contains('rm-dim')) leader.setAttribute('stroke-opacity', '.08');
                    leaderLayer.append(leader);
                }
            });
        }
        function timePosition(stamp) {
            if (!datePositions.length) return 0;
            if (stamp <= datePositions[0].stamp) return datePositions[0].x;
            const last = datePositions[datePositions.length - 1];
            if (stamp >= last.stamp) return last.x;
            let index = 1;
            while (datePositions[index].stamp < stamp) index++;
            const a = datePositions[index - 1], b = datePositions[index];
            return a.x + (b.x - a.x) * (stamp - a.stamp) / (b.stamp - a.stamp);
        }
        function buildAxis() {
            const unique = new Map();
            network.nodes.forEach(function (node) {
                const date = String(node.published_date || node.date || '').slice(0, 10), stamp = Date.parse(date + 'T00:00:00Z');
                if (Number.isFinite(stamp)) unique.set(date, {date: date, stamp: stamp, x: pointById.get(node.id).x});
            });
            datePositions = Array.from(unique.values()).sort(function (a, b) { return a.stamp - b.stamp; });
            dates = datePositions.map(function (item) { return item.date; });
            const candidates = [];
            if (datePositions.length) {
                candidates.push(datePositions[0].stamp);
                const end = new Date(datePositions[datePositions.length - 1].stamp);
                let cursor = new Date(datePositions[0].stamp); cursor.setUTCDate(1); cursor.setUTCMonth(cursor.getUTCMonth() + 1);
                while (cursor < end) { candidates.push(cursor.getTime()); cursor.setUTCMonth(cursor.getUTCMonth() + 1); }
                candidates.push(datePositions[datePositions.length - 1].stamp);
            }
            tickDates = candidates;
        }
        function drawAxis() {
            axis.replaceChildren();
            let last = -100;
            tickDates.forEach(function (stamp, index) {
                const x = timePosition(stamp) * camera.zoom + camera.x;
                if (x < 24 || x > width - 24 || x - last < 84 && index !== tickDates.length - 1) return;
                if (x - last < 55) return;
                const date = new Date(stamp), tick = html('span', 'rm-time-tick');
                tick.textContent = String(date.getUTCFullYear()) + '.' + String(date.getUTCMonth() + 1).padStart(2, '0') + (index === tickDates.length - 1 ? '.' + String(date.getUTCDate()).padStart(2, '0') : '');
                tick.style.left = x + 'px'; axis.append(tick); last = x;
            });
        }
        function setSelection(id, options) {
            selected = nodeAllowed(id) ? id : null;
            selectedLink = options && linkAllowed(links.get(options.linkId)) ? options.linkId : null;
            paint();
        }
        function selectNode(id) { if (!nodeAllowed(id)) return; setSelection(id); if (callbacks.onSelect) callbacks.onSelect(id); }
        function clear() { setSelection(null); if (callbacks.onClear) callbacks.onClear(); }
        function focus(id) {
            const point = pointById.get(id);
            if (!point || !nodeAllowed(id)) return;
            camera.zoom = clamp(Math.max(camera.zoom, baseZoom * 1.45), overviewZoom() * .65, baseZoom * 6);
            camera.x = width / 2 - point.x * camera.zoom; camera.y = (height - AXIS) / 2 - point.y * camera.zoom;
            viewportChanged(false);
        }
        function zoomAt(factor, x, y) {
            const next = clamp(camera.zoom * factor, overviewZoom() * .65, baseZoom * 6);
            const ratio = next / camera.zoom;
            const center = worldHeight / 2 * camera.zoom + camera.y;
            camera.x = x - (x - camera.x) * ratio;
            camera.y = camera.zoom <= baseZoom && next <= baseZoom ? center - worldHeight / 2 * next : y - (y - camera.y) * ratio;
            camera.zoom = next;
            viewportChanged(false);
        }
        function findNode(target) { return target && target.closest ? target.closest('[data-rm-node]') : null; }
        function findLink(target) { return target && target.closest ? target.closest('[data-rm-link]') : null; }
        function onDown(event) {
            if (event.button !== 0 || event.isPrimary === false) return;
            const node = findNode(event.target), link = findLink(event.target);
            pointer = {id: event.pointerId, startX: event.clientX, startY: event.clientY, x: camera.x, y: camera.y, moved: false, node: node && node.dataset.rmNode, link: link && link.dataset.rmLink};
            root.setPointerCapture(event.pointerId);
            if (!node) root.focus({preventScroll: true});
        }
        function onMove(event) {
            if (!pointer || pointer.id !== event.pointerId) return;
            const dx = event.clientX - pointer.startX, dy = event.clientY - pointer.startY;
            if (!pointer.moved && Math.hypot(dx, dy) < 4) return;
            pointer.moved = true; root.classList.add('rm-dragging');
            camera.x = pointer.x + dx; camera.y = pointer.y + dy;
            viewportChanged(true);
        }
        function onUp(event) {
            if (!pointer || pointer.id !== event.pointerId) return;
            const action = pointer; pointer = null; root.classList.remove('rm-dragging');
            if (root.hasPointerCapture(event.pointerId)) root.releasePointerCapture(event.pointerId);
            if (action.moved) { viewportChanged(false); return; }
            if (action.node) selectNode(action.node);
            else if (action.link && linkAllowed(links.get(action.link))) { setSelection(null, {linkId: action.link}); if (callbacks.onLinkSelect) callbacks.onLinkSelect(action.link); }
            else clear();
        }
        function onCancel() { pointer = null; root.classList.remove('rm-dragging'); }
        function onWheel(event) {
            event.preventDefault();
            const rect = root.getBoundingClientRect();
            zoomAt(Math.exp(-clamp(event.deltaY, -120, 120) * .0025), event.clientX - rect.left, event.clientY - rect.top);
        }
        function updateRovingFocus() {
            const visible = Array.from(nodeViews.entries()).filter(function (entry) { return nodeAllowed(entry[0]) && !entry[1].button.hidden; });
            if (!visible.some(function (entry) { return entry[0] === focused; })) {
                visible.sort(function (a, b) {
                    const distance = function (entry) {
                        const point = entry[1].point || project(pointById.get(entry[0]));
                        return Math.hypot(point.x - width / 2, point.y - (height - AXIS) / 2);
                    };
                    return distance(a) - distance(b) || a[0].localeCompare(b[0]);
                });
                focused = visible.length ? visible[0][0] : null;
            }
            nodeViews.forEach(function (view, id) { view.button.tabIndex = id === focused && nodeAllowed(id) ? 0 : -1; });
        }
        function setFilter(ids) {
            filtered = ids == null ? null : new Set(ids);
            filterChanged = true;
            if (!nodeAllowed(selected)) selected = null;
            if (!linkAllowed(links.get(selectedLink))) selectedLink = null;
            const active = findNode(document.activeElement);
            if (active && !nodeAllowed(active.dataset.rmNode)) root.focus({preventScroll: true});
            if (!nodeAllowed(focused)) focused = null;
            paint();
            updateRovingFocus();
            schedule(true);
        }
        function keyboardFocus(id) {
            if (!nodeAllowed(id)) return;
            focused = id;
            nodeViews.forEach(function (view, key) { view.button.tabIndex = key === id ? 0 : -1; });
            const point = project(pointById.get(id));
            if (point.x < 70 || point.x > width - 70 || point.y < 55 || point.y > height - AXIS - 55) {
                camera.x += width / 2 - point.x; camera.y += (height - AXIS) / 2 - point.y; draw(); placeNames(); emitViewport();
            }
            nodeViews.get(id).button.focus({preventScroll: true});
        }
        function onKey(event) {
            if (event.altKey || event.ctrlKey || event.metaKey) return;
            if (event.key === 'Escape') { event.preventDefault(); clear(); return; }
            if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomAt(1.25, width / 2, (height - AXIS) / 2); return; }
            if (event.key === '-') { event.preventDefault(); zoomAt(.8, width / 2, (height - AXIS) / 2); return; }
            if (event.key === 'Home') { event.preventDefault(); fit(); return; }
            if (event.key === 'End') {
                event.preventDefault();
                const latest = network.nodes.filter(function (node) { return nodeAllowed(node.id); }).sort(function (a, b) {
                    return String(b.published_date || b.date || '').localeCompare(String(a.published_date || a.date || '')) || a.id.localeCompare(b.id);
                })[0];
                if (latest) { focus(latest.id); keyboardFocus(latest.id); } return;
            }
            const target = findNode(event.target), candidate = target ? target.dataset.rmNode : focused || selected;
            const current = nodeAllowed(candidate) ? candidate : null;
            if (event.key === 'Enter' || event.key === ' ') {
                if (current && nodes.has(current)) { event.preventDefault(); selectNode(current); } return;
            }
            const directions = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}, direction = directions[event.key];
            if (!direction) return;
            event.preventDefault();
            if (event.shiftKey) { camera.x -= direction[0] * 80; camera.y -= direction[1] * 80; viewportChanged(false); return; }
            const origin = current && pointById.has(current) ? project(pointById.get(current)) : {x: width / 2, y: (height - AXIS) / 2};
            let nearest = null, score = Infinity;
            pointById.forEach(function (point, id) {
                if (id === current || !nodeAllowed(id)) return;
                const candidate = project(point), dx = candidate.x - origin.x, dy = candidate.y - origin.y;
                const ahead = dx * direction[0] + dy * direction[1], side = Math.abs(dx * direction[1] - dy * direction[0]);
                if (ahead < 2) return;
                const value = Math.hypot(dx, dy) + side * 2.1;
                if (value < score) { score = value; nearest = id; }
            });
            if (nearest) keyboardFocus(nearest);
        }
        function onClick(event) {
            if (event.detail) return;
            const node = findNode(event.target);
            if (node) selectNode(node.dataset.rmNode);
        }
        function resize() {
            const nextWidth = root.clientWidth, nextHeight = root.clientHeight;
            if (!nextWidth || !nextHeight || nextWidth === width && nextHeight === height) return;
            const oldWidth = width, oldHeight = height; width = nextWidth; height = nextHeight;
            if (initial || !oldWidth) { fit(); initial = false; }
            else { camera.x += (width - oldWidth) / 2; camera.y += (height - oldHeight) / 2; viewportChanged(false); }
        }
        root.addEventListener('pointerdown', onDown); root.addEventListener('pointermove', onMove); root.addEventListener('pointerup', onUp);
        root.addEventListener('pointercancel', onCancel); root.addEventListener('lostpointercapture', onCancel);
        root.addEventListener('wheel', onWheel, {passive: false}); root.addEventListener('keydown', onKey); root.addEventListener('click', onClick);
        const observer = new ResizeObserver(resize); observer.observe(root); resize();

        return {
            render: function (value) {
                network = value || {nodes: [], links: []}; network.nodes = network.nodes || []; network.links = network.links || [];
                const stats = network.stats || {}, observedMax = Math.max.apply(null, [1].concat(network.nodes.map(citationCount)));
                maxCitations = Math.max(observedMax, Number(stats.max_citation_count) || 0, 1);
                emphasisThreshold = Math.max(1, Number(stats.citation_emphasis_threshold) || 10);
                majorThreshold = Math.max(emphasisThreshold, Number(stats.citation_major_threshold) || 34);
                nodes = new Map(network.nodes.map(function (node) { return [node.id, node]; }));
                links = new Map(network.links.map(function (link) { return [link.id, link]; }));
                localLinks = new Map(network.nodes.map(function (node) { return [node.id, []]; }));
                network.links.forEach(function (link) {
                    if (localLinks.has(link.source)) localLinks.get(link.source).push(link);
                    if (localLinks.has(link.target)) localLinks.get(link.target).push(link);
                });
                localLinks.forEach(function (items, id) {
                    localLinks.set(id, new Set(items.slice().sort(function (a, b) { return b.weight - a.weight || a.id.localeCompare(b.id); }).slice(0, 6).map(function (link) { return link.id; })));
                });
                if (!nodeAllowed(selected)) selected = null; if (!linkAllowed(links.get(selectedLink))) selectedLink = null;
                buildPositions(); build(); buildAxis(); startView();
            },
            setSelection: setSelection,
            setFilter: setFilter,
            setHighlight: function (ids) { highlighted = ids == null ? null : new Set(ids); paint(); },
            focus: focus,
            overview: fit,
            zoomBy: function (factor) { if (Number.isFinite(factor) && factor > 0) zoomAt(factor, width / 2, (height - AXIS) / 2); },
            destroy: function () {
                destroyed = true; observer.disconnect(); global.cancelAnimationFrame(frame); global.clearTimeout(labelTimer); root.remove();
            }
        };
    }

    global.RecommendationMap = {create: create};
})(window);
