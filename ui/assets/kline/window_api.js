        _applyMarketChrome();
        chart.setOption(buildOption());
        _installSmoothWheelZoom();
        _updateToolbar(rawData.dates.length - 1, false);

        let lastAppliedSnapshotKey = null;
        let lastAppliedSnapshotMeta = null;
        let lastRenderedSnapshotMeta = null;
        let pendingRenderedSnapshotMeta = null;
        let pendingRuntimeSnapshot = null;

        chart.on('rendered', function () {
            if (!pendingRenderedSnapshotMeta) return;
            lastRenderedSnapshotMeta = pendingRenderedSnapshotMeta;
            pendingRenderedSnapshotMeta = null;
        });

        function _snapshotMeta(payload) {
            const value = payload || {};
            const data = value.data || {};
            const rawGeneration = value.generation !== undefined ? value.generation : data.generation;
            const rawPoints = value.points !== undefined
                ? value.points
                : (Array.isArray(data.dates) ? data.dates.length : 0);
            const snapshotVersion = value.snapshotVersion !== undefined
                ? value.snapshotVersion
                : (value.snapshot_version !== undefined ? value.snapshot_version : null);
            const generation = Number(rawGeneration);
            const points = Number(rawPoints);
            const meta = {
                windowId: String(payload && (payload.windowId || payload.window_id) || data.windowId || data.window_id || ''),
                generation: Number.isFinite(generation) ? generation : 0,
                code: String(value.code || data.code || ''),
                points: Number.isFinite(points) ? points : 0,
                snapshotVersion: snapshotVersion,
                snapshotKey: null
            };
            if (snapshotVersion !== null && snapshotVersion !== undefined) {
                meta.snapshotKey = [
                    meta.windowId,
                    meta.generation,
                    meta.code,
                    String(snapshotVersion)
                ].join('|');
            }
            return meta;
        }

        function _snapshotAck(meta, state) {
            const flags = state || {};
            return {
                ok: flags.ok !== false,
                applied: flags.applied === true,
                queued: flags.queued === true,
                duplicate: flags.duplicate === true,
                runtimeActive: runtimeActive,
                windowId: meta.windowId,
                generation: meta.generation,
                code: meta.code,
                points: meta.points,
                snapshotVersion: meta.snapshotVersion,
                error: flags.error || ''
            };
        }

        function _snapshotMetaMatches(left, right) {
            if (!left || !right) return false;
            return left.windowId === right.windowId
                && left.generation === right.generation
                && left.code === right.code
                && left.points === right.points
                && String(left.snapshotVersion) === String(right.snapshotVersion);
        }

        function _applySnapshotNow(payload, meta) {
            rawData = payload.data;
            _applyMarketChrome();
            if (payload.title) {
                document.title = payload.title;
            }

            const currentOption = chart.getOption ? chart.getOption() : null;
            const dataZoom = currentOption && currentOption.dataZoom ? currentOption.dataZoom : null;
            const nextOption = buildOption();
            if (dataZoom) {
                nextOption.dataZoom = dataZoom;
            }
            pendingRenderedSnapshotMeta = meta;
            chart.setOption(nextOption, {
                notMerge: false,
                lazyUpdate: false,
                replaceMerge: ['series']
            });
            _queueRuntimeResize();
            lastToolbarIdx = -1;
            _updateToolbar(rawData.dates.length - 1, false);
            _clearPointerCloseMarker();
            lastAppliedSnapshotKey = meta.snapshotKey;
            lastAppliedSnapshotMeta = meta;
            return _snapshotAck(meta, { applied: true });
        }

        window.applySnapshot = function (payload) {
            const meta = _snapshotMeta(payload);
            if (!payload || !payload.data) {
                return _snapshotAck(meta, { ok: false, error: 'invalid_snapshot' });
            }
            if (meta.snapshotKey !== null && meta.snapshotKey === lastAppliedSnapshotKey) {
                return _snapshotAck(meta, { duplicate: true });
            }
            if (!runtimeActive) {
                pendingRuntimeSnapshot = { payload: payload, meta: meta };
                return _snapshotAck(meta, { queued: true });
            }
            pendingRuntimeSnapshot = null;
            return _applySnapshotNow(payload, meta);
        };

        window.getSnapshotRenderState = function (payload) {
            const requested = _snapshotMeta(payload);
            const ack = _snapshotAck(requested, {});
            ack.rendered = _snapshotMetaMatches(requested, lastRenderedSnapshotMeta);
            return ack;
        };

        function _setParticlesActive(active) {
            chart.setOption({
                series: [{
                    id: 'volumeSpikeParticles',
                    data: active ? buildVolumeSpikeParticles() : []
                }]
            }, false, true);
        }

        window.setRuntimeActive = function (payload) {
            const requested = payload && typeof payload === 'object' ? payload.active : payload;
            _setRuntimeFrameActive(requested !== false);
            let replayAck = null;
            if (runtimeActive && pendingRuntimeSnapshot) {
                const pending = pendingRuntimeSnapshot;
                pendingRuntimeSnapshot = null;
                replayAck = _applySnapshotNow(pending.payload, pending.meta);
            }
            if (!replayAck) {
                _setParticlesActive(runtimeActive);
            }
            const meta = replayAck
                ? _snapshotMeta({
                    windowId: replayAck.windowId,
                    generation: replayAck.generation,
                    code: replayAck.code,
                    points: replayAck.points,
                    snapshotVersion: replayAck.snapshotVersion
                })
                : (lastAppliedSnapshotMeta || _snapshotMeta(null));
            const ack = _snapshotAck(meta, { applied: !!(replayAck && replayAck.applied) });
            ack.active = runtimeActive;
            return ack;
        };

        window.resetForLease = function (payload) {
            _setRuntimeFrameActive(false);
            pendingRuntimeSnapshot = null;
            lastAppliedSnapshotKey = null;
            lastAppliedSnapshotMeta = null;
            lastRenderedSnapshotMeta = null;
            pendingRenderedSnapshotMeta = null;
            rawData = {
                title: String(payload && payload.title || 'K线'),
                dates: [], klines: [], vols: [], volMa20: [], macd: [], diff: [], dea: [],
                ma10: [], ma20: [], ma50: [], ma150: [], ma200: [], maStyles: {},
                marketState: { market: '', status: '', active: false, live: false },
                vcpMarkers: null, vcpLines: null, vcpArea: null, earningsMarkers: null
            };
            glassFused = false;
            chart.clear();
            _applyMarketChrome();
            chart.setOption(buildOption());
            _resetToolbar();
            _clearPointerCloseMarker();
            return { ok: true, reset: true };
        };

        window.applyTheme = function (payload) {
            const t = payload && payload.theme ? payload.theme : payload;
            if (!t) return false;
            themeState = t;
            if (t.up_color) upColor = t.up_color;
            if (t.down_color) downColor = t.down_color;
            _applyCssTheme(themeState);
            _applyMarketChrome();

            const currentOption = chart.getOption ? chart.getOption() : null;
            const dataZoom = currentOption && currentOption.dataZoom ? currentOption.dataZoom : null;
            const nextOption = buildOption();
            if (dataZoom) {
                nextOption.dataZoom = dataZoom;
            }
            chart.setOption(nextOption, { notMerge: false, lazyUpdate: true, replaceMerge: ['series'] });
            _queueRuntimeResize();
            _updateToolbar(rawData.dates.length - 1, false);
            _clearPointerCloseMarker();
            return true;
        };

        window.applyMarketState = function (payload) {
            const state = payload && payload.marketState ? payload.marketState : payload;
            if (!state) return false;
            rawData.marketState = state;
            _applyMarketChrome();
            chart.setOption({
                backgroundColor: _chartBackgroundColor()
            }, false, true);
            return true;
        };

        window.setGlassMode = function (payload) {
            glassFused = !!(payload && payload.enabled);
            _applyMarketChrome();
            chart.setOption({ backgroundColor: _chartBackgroundColor() }, false, true);
            _queueRuntimeResize();
            return true;
        };

        window.addEventListener('resize', function () {
            _queueRuntimeResize();
        });
