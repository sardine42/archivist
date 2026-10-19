(function (global) {
    'use strict';

    const DAY = 86400000;

    function text(value) {
        return value == null ? '' : String(value);
    }

    function key(value) {
        return text(value).normalize('NFKC').trim().toLocaleLowerCase();
    }

    function list(value) {
        return Array.isArray(value) ? value.slice() : (value ? [value] : []);
    }

    function dateValue(value) {
        const date = text(value).slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
        const stamp = Date.parse(date + 'T00:00:00Z');
        if (!Number.isFinite(stamp)) return null;
        return new Date(stamp).toISOString().slice(0, 10) === date ? stamp : null;
    }

    function number(value) {
        if (value == null || value === '') return null;
        const result = Number(value);
        return Number.isFinite(result) ? result : null;
    }

    function count(value, fallback) {
        const result = number(value);
        return result == null ? fallback : Math.max(0, Math.floor(result));
    }

    function create(payload) {
        const source = payload || {};
        const nodes = new Map();
        const names = new Map();
        const ids = new Map();
        const searchRecords = new Map();
        const dates = new Map();

        list(source.nodes).forEach(function (raw) {
            if (!raw || raw.id == null) return;
            const node = Object.assign({}, raw, {
                id: text(raw.id),
                aliases: list(raw.aliases),
                category: list(raw.category),
                tags: list(raw.tags),
            });
            nodes.set(node.id, node);
            ids.set(key(node.id), node);
            dates.set(node.id, dateValue(node.published_date));
        });

        function score(node) {
            const reading = number(node.reading_score);
            return reading == null ? (number(node.score) || 0) : reading;
        }

        function stable(a, b) {
            return text(a.model_name || a.title || a.id).localeCompare(
                text(b.model_name || b.title || b.id), 'zh-CN', {numeric: true}
            ) || a.id.localeCompare(b.id);
        }

        function newest(a, b) {
            const aDate = dates.get(a.id);
            const bDate = dates.get(b.id);
            if (aDate !== bDate) {
                if (aDate == null) return 1;
                if (bDate == null) return -1;
                return bDate - aDate;
            }
            return 0;
        }

        function oldest(a, b) {
            const aDate = dates.get(a.id);
            const bDate = dates.get(b.id);
            if (aDate !== bDate) {
                if (aDate == null) return 1;
                if (bDate == null) return -1;
                return aDate - bDate;
            }
            return stable(a, b);
        }

        // Exact IDs always win over aliases. Ambiguous aliases prefer an
        // archived reading rather than an unannotated reference node.
        Array.from(nodes.values()).sort(function (a, b) {
            return Number(!!b.has_reading) - Number(!!a.has_reading) || stable(a, b);
        }).forEach(function (node) {
            const aliases = [node.model_name].concat(node.aliases).map(key).filter(Boolean);
            aliases.forEach(function (alias) {
                if (!names.has(alias)) names.set(alias, node);
            });
            searchRecords.set(node.id, {
                names: aliases,
                title: key(node.title),
                extra: key([node.summary].concat(node.tags).join(' ')),
            });
        });

        function resolve(value) {
            const raw = text(value).trim();
            const canonical = raw.startsWith('paper:') ? raw.replace(/v\d+$/, '') : raw;
            return nodes.get(canonical) || ids.get(key(canonical)) || names.get(key(raw)) || null;
        }

        function matches(node, options) {
            const settings = options || {};
            const category = key(settings.category);
            const company = key(settings.company);
            return (!category || node.category.some(function (item) { return key(item) === category; })) &&
                (!company || key(node.company) === company);
        }

        const lineages = list(source.lineages).filter(Boolean).map(function (raw) {
            return Object.assign({}, raw, {
                members: list(raw.members),
                related_members: list(raw.related_members),
            });
        });
        const lineageById = new Map(lineages.map(function (item) { return [text(item.id), item]; }));
        const memberships = new Map();
        lineages.forEach(function (definition) {
            definition.members.forEach(function (id) {
                const node = resolve(id);
                if (!node) return;
                if (!memberships.has(node.id)) memberships.set(node.id, new Set());
                memberships.get(node.id).add(definition.id);
            });
        });

        function sharedLineages(a, b) {
            const first = memberships.get(a.id) || new Set();
            const second = memberships.get(b.id) || new Set();
            return lineages.filter(function (definition) {
                return first.has(definition.id) && second.has(definition.id);
            });
        }

        const adjacency = new Map();
        list(source.relations).forEach(function (raw) {
            if (!raw || (raw.type !== 'citation' && raw.type !== 'comparison')) return;
            const from = resolve(raw.source);
            const to = resolve(raw.target);
            if (!from || !to) return;
            const relation = Object.assign({}, raw, {source: from.id, target: to.id});
            [from.id, to.id].forEach(function (id, index) {
                if (index === 1 && from.id === to.id) return;
                if (!adjacency.has(id)) adjacency.set(id, []);
                adjacency.get(id).push(relation);
            });
        });

        const readNodes = Array.from(nodes.values()).filter(function (node) { return !!node.has_reading; });

        function search(query, limit) {
            const phrase = key(query);
            if (!phrase) return [];
            const terms = phrase.split(/\s+/).filter(Boolean);
            const candidates = [];
            nodes.forEach(function (node) {
                const record = searchRecords.get(node.id);
                let rank;
                if (record.names.some(function (name) { return name === phrase; })) rank = 0;
                else if (record.names.some(function (name) { return name.startsWith(phrase); })) rank = 1;
                else if (record.names.some(function (name) { return name.includes(phrase); })) rank = 2;
                else if (record.title.includes(phrase)) rank = 3;
                else {
                    const fields = record.names.concat([record.title, record.extra]);
                    if (!terms.every(function (term) {
                        return fields.some(function (field) { return field.includes(term); });
                    })) return;
                    rank = terms.every(function (term) {
                        return record.names.concat([record.title]).some(function (field) { return field.includes(term); });
                    }) ? 3 : 4;
                }
                candidates.push({node: node, rank: rank});
            });
            candidates.sort(function (a, b) {
                return a.rank - b.rank || Number(!!b.node.has_reading) - Number(!!a.node.has_reading) ||
                    score(b.node) - score(a.node) || newest(a.node, b.node) || stable(a.node, b.node);
            });
            return candidates.slice(0, count(limit, 8)).map(function (item) { return item.node; });
        }

        function frontier(options) {
            const settings = Object.assign({days: 90, minScore: 8}, options || {});
            const asOf = dateValue(source.as_of);
            if (asOf == null) return [];
            const days = Math.max(1, count(settings.days, 90));
            const cutoff = asOf - (days - 1) * DAY;
            const minimum = number(settings.minScore) || 0;
            return readNodes.filter(function (node) {
                const published = dates.get(node.id);
                const reading = number(node.reading_score);
                return published != null && published >= cutoff && published <= asOf &&
                    (minimum <= 0 || (reading != null && reading >= minimum)) && matches(node, settings);
            }).sort(function (a, b) {
                return newest(a, b) || (number(b.reading_score) || 0) - (number(a.reading_score) || 0) || stable(a, b);
            });
        }

        function lineage(id, options) {
            const definition = lineageById.get(text(id));
            if (!definition) return {definition: null, members: [], related: []};
            function collect(memberIds, excluded) {
                const found = new Map();
                memberIds.forEach(function (memberId) {
                    const node = resolve(memberId);
                    if (node && !excluded.has(node.id) && matches(node, options)) found.set(node.id, node);
                });
                return Array.from(found.values()).sort(oldest);
            }
            const allCore = new Set(definition.members.map(resolve).filter(Boolean).map(function (node) { return node.id; }));
            return {
                definition: definition,
                members: collect(definition.members, new Set()),
                related: collect(definition.related_members, allCore),
            };
        }

        function evidence(first, second) {
            const a = resolve(first);
            const b = resolve(second);
            if (!a || !b) return [];
            return (adjacency.get(a.id) || []).filter(function (relation) {
                return (relation.source === a.id && relation.target === b.id) ||
                    (relation.source === b.id && relation.target === a.id);
            });
        }

        function related(id, options) {
            const settings = Object.assign({limitPerSide: 6, relation: 'all'}, options || {});
            const focus = resolve(id);
            const result = {focus: focus, before: [], after: [], beforeTotal: 0, afterTotal: 0, similar: []};
            if (!focus) return result;
            const neighbors = new Map();
            const directIds = new Set();
            (adjacency.get(focus.id) || []).forEach(function (relation) {
                const otherId = relation.source === focus.id ? relation.target : relation.source;
                if (otherId === focus.id) return;
                directIds.add(otherId);
                if (settings.relation !== 'all' && relation.type !== settings.relation) return;
                const node = nodes.get(otherId);
                if (!matches(node, settings)) return;
                if (!neighbors.has(otherId)) neighbors.set(otherId, {node: node, relations: [], before: false});
                const neighbor = neighbors.get(otherId);
                neighbor.relations.push(relation);
                // Citations point to earlier work; comparison arrows point
                // from the baseline toward the model reported as better.
                if ((relation.type === 'citation' && relation.source === focus.id) ||
                    (relation.type === 'comparison' && relation.target === focus.id)) neighbor.before = true;
            });
            const before = [];
            const after = [];
            neighbors.forEach(function (neighbor) {
                (neighbor.before ? before : after).push({node: neighbor.node, relations: neighbor.relations});
            });
            function order(a, b) {
                return Number(sharedLineages(focus, b.node).length > 0) - Number(sharedLineages(focus, a.node).length > 0) ||
                    Number(!!b.node.has_reading) - Number(!!a.node.has_reading) || b.relations.length - a.relations.length ||
                    newest(a.node, b.node) || score(b.node) - score(a.node) || stable(a.node, b.node);
            }
            const maximum = count(settings.limitPerSide, 6);
            result.beforeTotal = before.length;
            result.afterTotal = after.length;
            result.before = before.sort(order).slice(0, maximum);
            result.after = after.sort(order).slice(0, maximum);
            const focusTags = new Set(focus.tags.map(key));
            result.similar = readNodes.filter(function (node) {
                return node.id !== focus.id && !directIds.has(node.id) && matches(node, settings);
            }).map(function (node) {
                return {
                    node: node,
                    sharedTags: Array.from(new Set(node.tags.filter(function (tag) { return focusTags.has(key(tag)); }))),
                    sharedLineages: sharedLineages(focus, node),
                };
            }).filter(function (item) {
                return item.sharedTags.length || item.sharedLineages.length;
            }).sort(function (a, b) {
                return b.sharedLineages.length - a.sharedLineages.length || b.sharedTags.length - a.sharedTags.length ||
                    newest(a.node, b.node) || score(b.node) - score(a.node) || stable(a.node, b.node);
            }).slice(0, 6);
            return result;
        }

        return {nodes: nodes, lineages: lineages, readNodes: readNodes, resolve: resolve,
            search: search, frontier: frontier, lineage: lineage, related: related, evidence: evidence};
    }

    global.GraphExplorerModel = {create: create};
})(window);
