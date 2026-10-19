(function (global) {
    'use strict';

    const DAY = 86400000;
    const RECENT_MONTH_WIDTH = 150;
    const HISTORIC_MONTH_WIDTH = 80;
    const MIN_DISTANCE = 16;
    const TOP = 28;
    const BOTTOM = 22;
    const LANE_GAP = 6;
    const POINT_PADDING = 12;
    const FIRST_DATE_X = 140;
    const DEFINITIONS = [
        {id: 'lineage:onerec', label: 'OneRec', color: '#b66d50', minimumHeight: 62},
        {id: 'lineage:semantic-id', label: 'SID · 语义 ID', color: '#638978', minimumHeight: 112},
        {id: 'category:generative-rec', label: '生成式推荐', color: '#6b89b3', minimumHeight: 80},
        {id: 'category:discriminative-rec', label: '判别式推荐', color: '#b18a65', minimumHeight: 88},
        {id: 'category:llm', label: 'LLM', color: '#9781ad', minimumHeight: 76},
        {id: 'category:other', label: '其他研究', color: '#779391', minimumHeight: 64}
    ];

    function nodeId(node) { return String(node && (node.id || node.model_name) || ''); }
    function array(value) {
        if (Array.isArray(value)) return value;
        if (value instanceof Map) return Array.from(value.values());
        return [];
    }
    function stamp(value) {
        if (typeof value === 'number') return Number.isFinite(value) ? value : null;
        const text = String(value || '').slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
        const result = Date.parse(text + 'T00:00:00Z');
        return Number.isFinite(result) && new Date(result).toISOString().slice(0, 10) === text ? result : null;
    }
    function monthIndex(value) {
        const date = new Date(value);
        return date.getUTCFullYear() * 12 + date.getUTCMonth();
    }
    function monthStamp(value) { return Date.UTC(Math.floor(value / 12), value % 12, 1); }
    function monthText(value) { return Math.floor(value / 12) + '.' + String(value % 12 + 1).padStart(2, '0'); }
    function compareId(a, b) { return a.id < b.id ? -1 : a.id > b.id ? 1 : 0; }

    // With x fixed by date, each nearby circle excludes one interval of y values.
    // Choosing the closest available y keeps each track compact without a rigid grid.
    function freeY(x, previous, preferred, lower, upper, distance) {
        const intervals = [];
        previous.forEach(function (point) {
            const dx = Math.abs(point.x - x);
            if (dx >= distance) return;
            const half = Math.sqrt(distance * distance - dx * dx) + 0.001;
            intervals.push([point.y - half, point.y + half]);
        });
        intervals.sort(function (a, b) { return a[0] - b[0]; });
        const merged = [];
        intervals.forEach(function (interval) {
            const last = merged[merged.length - 1];
            if (last && interval[0] <= last[1]) last[1] = Math.max(last[1], interval[1]);
            else merged.push(interval.slice());
        });
        const candidates = [Math.max(lower, Math.min(upper, preferred))];
        merged.forEach(function (interval) { candidates.push(interval[0], interval[1]); });
        candidates.sort(function (a, b) { return Math.abs(a - preferred) - Math.abs(b - preferred) || a - b; });
        for (let i = 0; i < candidates.length; i += 1) {
            const candidate = candidates[i];
            if (candidate < lower || candidate > upper) continue;
            if (!merged.some(function (interval) { return candidate > interval[0] && candidate < interval[1]; })) return candidate;
        }
        return null;
    }

    function create(options) {
        const input = options || {};
        const source = new Map();
        array(input.nodes).forEach(function (node) { const key = nodeId(node); if (key && !source.has(key)) source.set(key, node); });
        const lineages = array(input.lineages);
        const oneRec = new Set(), sid = new Set();
        lineages.forEach(function (definition) {
            const key = String(definition.id || '').toLowerCase();
            const isOneRec = key === 'onerec' || /^onerec$/i.test(String(definition.label || '').trim());
            const isSid = key === 'semantic-id' || key === 'sid';
            if (!isOneRec && !isSid) return;
            array(definition.members || definition.node_ids || definition.nodes).forEach(function (member) {
                const id = typeof member === 'object' ? nodeId(member) : String(member);
                (isOneRec ? oneRec : sid).add(id);
            });
        });
        const validDates = Array.from(source.values()).map(function (node) { return stamp(node.published_date); }).filter(function (value) { return value != null; });
        const asOf = stamp(input.asOf == null ? input.as_of : input.asOf);
        const anchor = asOf == null ? (validDates.length ? Math.max.apply(null, validDates) : Date.UTC(2026, 0, 1)) : asOf;
        const anchorMonth = monthIndex(anchor);
        const occupiedMonths = new Set(validDates.map(monthIndex));
        const firstMonth = occupiedMonths.size ? Math.min.apply(null, Array.from(occupiedMonths)) : anchorMonth;
        const lastMonth = occupiedMonths.size ? Math.max.apply(null, Array.from(occupiedMonths)) : anchorMonth;
        const monthSegments = new Map();
        const ticks = [];
        let cursor = FIRST_DATE_X;
        let previousTickYear = null;
        for (let month = firstMonth; month <= lastMonth; month += 1) {
            const recent = month >= anchorMonth - 11;
            const occupied = occupiedMonths.has(month);
            const width = occupied ? (recent ? RECENT_MONTH_WIDTH : HISTORIC_MONTH_WIDTH) : (recent ? 22 : 6);
            monthSegments.set(month, {x: cursor, width: width});
            const year = Math.floor(month / 12);
            if (occupied || month % 12 === 0) {
                const major = year !== previousTickYear;
                ticks.push({x: cursor, label: recent ? monthText(month) : (major ? String(year) : monthText(month)), major: major, date: monthStamp(month), year: year, month: month % 12 + 1, recent: recent, occupied: occupied});
                previousTickYear = year;
            }
            cursor += width;
        }
        function xForDate(value) {
            const time = stamp(value);
            if (time == null) return null;
            const month = monthIndex(time);
            let segment = monthSegments.get(month);
            if (!segment && month < firstMonth) segment = {x: FIRST_DATE_X - (firstMonth - month) * HISTORIC_MONTH_WIDTH, width: HISTORIC_MONTH_WIDTH};
            if (!segment) segment = {x: cursor + (month - lastMonth - 1) * RECENT_MONTH_WIDTH, width: RECENT_MONTH_WIDTH};
            const fraction = (time - monthStamp(month)) / (monthStamp(month + 1) - monthStamp(month));
            return segment.x + fraction * segment.width;
        }
        function laneId(node) {
            const key = nodeId(node);
            if (oneRec.has(key)) return 'lineage:onerec';
            if (sid.has(key)) return 'lineage:semantic-id';
            const category = (Array.isArray(node.category) ? node.category[0] : node.category) || 'other';
            return 'category:' + (['generative-rec', 'discriminative-rec', 'llm', 'other'].indexOf(category) >= 0 ? category : 'other');
        }
        const lanes = DEFINITIONS.map(function (definition) { return Object.assign({}, definition, {count: 0, readCount: 0, top: 0, bottom: 0, center: 0}); });
        const laneById = new Map(lanes.map(function (lane) { return [lane.id, lane]; }));
        const records = new Map(lanes.map(function (lane) { return [lane.id, []]; }));
        const positions = new Map();
        const undated = [];
        source.forEach(function (node, key) {
            const date = stamp(node.published_date);
            if (date == null) { undated.push(node); return; }
            records.get(laneId(node)).push({id: key, x: xForDate(date), y: 0, date: date, hasReading: !!node.has_reading});
        });
        let neededHeight = TOP + BOTTOM + LANE_GAP * (lanes.length - 1);
        lanes.forEach(function (lane) {
            const items = records.get(lane.id);
            items.sort(function (a, b) { return a.date - b.date || compareId(a, b); });
            const placed = [];
            items.forEach(function (item) {
                item.y = freeY(item.x, placed, 0, -Infinity, Infinity, MIN_DISTANCE);
                placed.push(item);
            });
            lane.count = items.length;
            lane.readCount = items.filter(function (item) { return item.hasReading; }).length;
            lane.localMin = items.length ? Math.min.apply(null, items.map(function (item) { return item.y; })) : 0;
            lane.localMax = items.length ? Math.max.apply(null, items.map(function (item) { return item.y; })) : 0;
            lane.height = Math.max(lane.minimumHeight, lane.localMax - lane.localMin + POINT_PADDING * 2);
            neededHeight += lane.height;
        });
        const height = Math.max(620, neededHeight);
        const extra = (height - neededHeight) / lanes.length;
        let top = TOP;
        lanes.forEach(function (lane) {
            lane.height += extra;
            lane.top = top;
            lane.bottom = top + lane.height;
            lane.center = (lane.top + lane.bottom) / 2;
            const shift = lane.center - (lane.localMin + lane.localMax) / 2;
            records.get(lane.id).forEach(function (item) {
                item.y += shift;
                positions.set(item.id, {x: item.x, y: item.y, laneId: lane.id, date: item.date});
            });
            delete lane.localMin; delete lane.localMax; delete lane.minimumHeight;
            top = lane.bottom + LANE_GAP;
        });
        const bounds = {x1: 0, x2: Math.max(760, cursor), y1: 0, y2: height, width: 0, height: height};
        let unknownIndex = 0;
        const unknownStep = 18;
        const unknownRows = Math.max(1, Math.floor((height - TOP - BOTTOM) / unknownStep));
        function placeUnknown(node) {
            const key = nodeId(node);
            const column = Math.floor(unknownIndex / unknownRows);
            const row = unknownIndex % unknownRows;
            positions.set(key, {x: -36 - column * unknownStep, y: TOP + unknownStep / 2 + row * unknownStep, laneId: 'reference', date: null});
            unknownIndex += 1;
        }
        undated.sort(function (a, b) { return compareId({id: nodeId(a)}, {id: nodeId(b)}); }).forEach(placeUnknown);
        const layout = {positions: positions, lanes: lanes, ticks: ticks, bounds: bounds, latestDate: null, initialRange: null, minDistance: MIN_DISTANCE, monthWidth: RECENT_MONTH_WIDTH, xForDate: xForDate, add: add};
        function updateBounds() {
            let left = 0, right = Math.max(760, cursor), latest = null;
            positions.forEach(function (point) {
                left = Math.min(left, point.x - 24);
                right = Math.max(right, point.x + 84);
                if (point.date != null && point.date <= anchor && (latest == null || point.date > latest)) latest = point.date;
            });
            bounds.x1 = left; bounds.x2 = right; bounds.width = right - left;
            layout.latestDate = latest;
        }
        function add(value) {
            const added = [];
            array(value).forEach(function (node) {
                const key = nodeId(node);
                if (!key || source.has(key)) return;
                const date = stamp(node.published_date);
                if (date == null) {
                    source.set(key, node); placeUnknown(node); added.push(key); return;
                }
                const lane = laneById.get(laneId(node));
                const x = xForDate(date);
                const allPoints = Array.from(positions.values());
                let y = freeY(x, allPoints, lane.center, lane.top + POINT_PADDING, lane.bottom - POINT_PADDING, MIN_DISTANCE);
                // A newly discovered dated reference can use another free position in
                // the same map when its primary track has no room, without moving papers.
                if (y == null) y = freeY(x, allPoints, lane.center, TOP + POINT_PADDING, height - BOTTOM - POINT_PADDING, MIN_DISTANCE);
                if (y == null) throw new RangeError('No space for another paper at this publication date');
                source.set(key, node);
                positions.set(key, {x: x, y: y, laneId: lane.id, date: date});
                lane.count += 1;
                if (node.has_reading) lane.readCount += 1;
                added.push(key);
            });
            updateBounds();
            return {ids: added, positions: positions, bounds: bounds, latestDate: layout.latestDate};
        }
        updateBounds();
        const latestX = layout.latestDate == null ? bounds.x2 - 84 : xForDate(layout.latestDate);
        layout.initialRange = {x1: Math.max(FIRST_DATE_X - 28, latestX - 1080), x2: latestX + 84, y1: 0, y2: height};
        return layout;
    }

    global.GraphLayout = {create: create};
}(typeof window === 'undefined' ? globalThis : window));
