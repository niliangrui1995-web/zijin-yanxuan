        const chart = echarts.init(document.getElementById('chart'));
        const toolbar = document.getElementById('toolbar');
        let pointerFrame = 0;
        let pendingPointerIdx = null;
        let lastToolbarIdx = -1;
        let toolbarFadeTimer = 0;

        function _clamp(value, min, max) {
            return Math.min(max, Math.max(min, value));
        }

        function _minZoomSpan() {
            const count = Math.max(rawData.dates.length, 1);
            return Math.min(100, Math.max(6, Math.min(30, 20 / count * 100)));
        }

        function _currentZoomRange() {
            const option = chart.getOption ? chart.getOption() : null;
            const zoom = option && option.dataZoom && option.dataZoom[0] ? option.dataZoom[0] : {};
            const start = Number(zoom.start !== undefined ? zoom.start : 55);
            const end = Number(zoom.end !== undefined ? zoom.end : 100);
            return {
                start: _clamp(Number.isFinite(start) ? start : 55, 0, 100),
                end: _clamp(Number.isFinite(end) ? end : 100, 0, 100)
            };
        }

        function _applyZoomRange(range) {
            chart.dispatchAction({
                type: 'dataZoom',
                dataZoomIndex: 0,
                start: _clamp(range.start, 0, 100),
                end: _clamp(range.end, 0, 100)
            });
        }

        function _installSmoothWheelZoom() {
            const zr = chart.getZr && chart.getZr();
            if (!zr || !zr.on) return;
            zr.on('mousewheel', function (params) {
                const event = params.event && params.event.event ? params.event.event : params.event;
                if (event && event.preventDefault) event.preventDefault();
                if (event && event.stopPropagation) event.stopPropagation();

                const deltaY = event && typeof event.deltaY === 'number'
                    ? event.deltaY
                    : -(params.wheelDelta || 0);
                const range = _currentZoomRange();
                const span = Math.max(range.end - range.start, _minZoomSpan());
                const nextSpan = _clamp(span * (deltaY > 0 ? 1.10 : 0.90), _minZoomSpan(), 100);
                let anchorPct = range.start + span / 2;

                try {
                    const point = chart.convertFromPixel({ xAxisIndex: 0 }, [params.offsetX, params.offsetY]);
                    if (Array.isArray(point) && Number.isFinite(point[0]) && rawData.dates.length > 1) {
                        anchorPct = _clamp(point[0] / (rawData.dates.length - 1) * 100, range.start, range.end);
                    }
                } catch (_err) {
                    anchorPct = range.start + span / 2;
                }

                const anchorRatio = _clamp((anchorPct - range.start) / Math.max(span, 0.001), 0, 1);
                let nextStart = anchorPct - nextSpan * anchorRatio;
                let nextEnd = nextStart + nextSpan;
                if (nextStart < 0) {
                    nextEnd -= nextStart;
                    nextStart = 0;
                }
                if (nextEnd > 100) {
                    nextStart -= nextEnd - 100;
                    nextEnd = 100;
                }
                _applyZoomRange({ start: nextStart, end: nextEnd });
            });
        }

        function _formatPrice(value) {
            const num = Number(value);
            return Number.isFinite(num) ? num.toFixed(2) : '-';
        }

        function _formatVolumeWan(value) {
            const volume = Number(value || 0);
            if (!Number.isFinite(volume)) return '-';
            const wan = volume / 10000;
            const absWan = Math.abs(wan);
            if (absWan >= 1000) return Math.round(wan).toLocaleString('zh-CN') + '\u4e07';
            if (absWan >= 100) return wan.toFixed(0) + '\u4e07';
            if (absWan >= 10) return wan.toFixed(1).replace(/\.0$/, '') + '\u4e07';
            return wan.toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1') + '\u4e07';
        }

        function _setText(id, value) {
            const el = document.getElementById(id);
            if (el) el.innerText = value;
        }

        function _setPointerCloseMarker(idx) {
            const kline = rawData.klines[idx];
            if (!kline) return;
            chart.setOption({
                series: [
                    {
                        id: 'pointerClose',
                        data: [[idx, Number(kline[1])]]
                    }
                ]
            }, false, true);
        }

        function _clearPointerCloseMarker() {
            chart.setOption({ series: [{ id: 'pointerClose', data: [] }] }, false, true);
        }

        function _updateToolbar(idx, fade) {
            if (idx < 0 || idx >= rawData.dates.length) return;
            const dt = rawData.dates[idx];
            const kline = rawData.klines[idx];
            if (!kline) return;

            const applyValues = function () {
                _setText('v-date', dt);
                _setText('v-open', _formatPrice(kline[0]));
                _setText('v-low', _formatPrice(kline[2]));
                _setText('v-high', _formatPrice(kline[3]));

                const prevClose = idx > 0 ? Number(rawData.klines[idx - 1][1]) : Number(kline[0]);
                const close = Number(kline[1]);
                const pct = prevClose > 0 ? ((close - prevClose) / prevClose * 100) : 0;
                const pctStr = pct >= 0 ? '+' + pct.toFixed(2) + '%' : pct.toFixed(2) + '%';
                const trendColor = pct >= 0 ? upColor : downColor;
                const closeEl = document.getElementById('v-close');
                if (closeEl) {
                    closeEl.innerText = _formatPrice(close);
                    closeEl.style.color = trendColor;
                }
                const pctEl = document.getElementById('v-pct');
                if (pctEl) {
                    pctEl.innerText = pctStr;
                    pctEl.style.color = trendColor;
                }

                const volEntry = rawData.vols[idx];
                const vol = volEntry && volEntry.value !== undefined ? volEntry.value : volEntry;
                _setText('v-vol', _formatVolumeWan(vol));

                const maKeys = ['ma10', 'ma20', 'ma50', 'ma150', 'ma200'];
                for (const key of maKeys) {
                    const val = rawData[key] ? rawData[key][idx] : null;
                    _setText('v-' + key, (val !== null && val !== undefined) ? Number(val).toFixed(2) : '-');
                }
            };

            if (!fade || !toolbar || idx === lastToolbarIdx) {
                applyValues();
                lastToolbarIdx = idx;
                return;
            }

            toolbar.classList.add('is-updating');
            window.clearTimeout(toolbarFadeTimer);
            toolbarFadeTimer = window.setTimeout(function () {
                applyValues();
                toolbar.classList.remove('is-updating');
            }, 50);
            lastToolbarIdx = idx;
        }

        function _schedulePointerUpdate(value) {
            const idx = Math.round(Number(value));
            if (!Number.isFinite(idx) || idx < 0 || idx >= rawData.dates.length) return;
            pendingPointerIdx = idx;
            if (pointerFrame) return;
            pointerFrame = requestAnimationFrame(function () {
                pointerFrame = 0;
                const nextIdx = pendingPointerIdx;
                pendingPointerIdx = null;
                if (nextIdx === null) return;
                _updateToolbar(nextIdx, true);
                _setPointerCloseMarker(nextIdx);
            });
        }

        chart.on('updateAxisPointer', function (event) {
            const axisInfo = event.axesInfo && event.axesInfo[0];
            if (axisInfo) {
                _schedulePointerUpdate(axisInfo.value);
            }
        });

        chart.on('globalout', function () {
            _clearPointerCloseMarker();
        });
