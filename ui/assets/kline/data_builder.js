        function splitData(rawData) {
            return {
                categoryData: rawData.dates,
                values: rawData.klines,
                volumes: rawData.vols
            };
        }

        const VCP_STAR_SYMBOL = 'path://M0 -13 L3 -3 L13 0 L3 3 L0 13 L-3 3 L-13 0 L-3 -3 Z';

        function _maLineStyle(key, baseWidth, baseOpacity, defaultType) {
            const styleMap = rawData.maStyles || {};
            const style = styleMap[key] || {};
            const width = Number.isFinite(Number(style.width)) ? Number(style.width) : baseWidth;
            const opacity = Number.isFinite(Number(style.opacity)) ? Number(style.opacity) : baseOpacity;
            const result = {
                width: width * MA_LINE_WIDTH_SCALE,
                color: themeState[key],
                opacity: opacity
            };
            const lineType = style.type || defaultType;
            if (lineType) result.type = lineType;
            if (style.emphasis) {
                result.shadowBlur = 9;
                result.shadowColor = themeState[key];
            }
            return result;
        }

        function _verticalGradient(topColor, bottomColor) {
            return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: topColor },
                { offset: 1, color: bottomColor }
            ]);
        }

        function _volumeRawValue(entry) {
            if (entry && typeof entry === 'object' && entry.value !== undefined) {
                return Number(entry.value || 0);
            }
            return Number(entry || 0);
        }

        function _volumeMetrics(idx) {
            const kline = rawData.klines[idx] || [];
            const volume = _volumeRawValue((rawData.vols || [])[idx]);
            const volMa = Number((rawData.volMa20 || [])[idx]);
            const isUp = Number(kline[1]) >= Number(kline[0]);
            let kind = 'normal';
            if (Number.isFinite(volMa) && volMa > 0 && volume > 0) {
                if (volume <= volMa / 3) kind = 'dry';
                else if (volume >= volMa * VOLUME_SPIKE_RATIO) kind = 'spike';
            }
            return { volume, volMa, isUp, kind };
        }

        function _volumeItemStyle(idx, kind) {
            const metrics = _volumeMetrics(idx);
            if (kind === 'dry') {
                return {
                    color: themeState.volume_dry,
                    opacity: 0.24
                };
            }
            if (metrics.kind === 'spike') {
                return {
                    color: _verticalGradient(themeState.volume_spike_top, themeState.volume_spike),
                    borderColor: themeState.volume_spike,
                    borderWidth: 0.8,
                    opacity: 0.96,
                    shadowBlur: 16,
                    shadowColor: themeState.volume_spike_shadow
                };
            }
            return {
                color: metrics.isUp ? upColor : downColor,
                opacity: 0.72
            };
        }

        function buildVolumeData(kind) {
            return (rawData.vols || []).map((entry, idx) => {
                const metrics = _volumeMetrics(idx);
                if (kind === 'dry' && metrics.kind !== 'dry') return null;
                if (kind !== 'dry' && metrics.kind === 'dry') return null;
                const base = entry && typeof entry === 'object' ? entry : { value: entry };
                return {
                    ...base,
                    itemStyle: {
                        ...(base.itemStyle || {}),
                        ..._volumeItemStyle(idx, kind)
                    }
                };
            });
        }

        function buildVolumeSpikeParticles() {
            return (rawData.vols || []).map((entry, idx) => {
                const metrics = _volumeMetrics(idx);
                if (metrics.kind !== 'spike') return null;
                return {
                    value: [rawData.dates[idx], metrics.volume],
                    symbolSize: Math.max(5, Math.min(10, metrics.volMa > 0 ? metrics.volume / metrics.volMa * 2.4 : 6)),
                    itemStyle: {
                        color: themeState.volume_spike_top,
                        shadowBlur: 12,
                        shadowColor: themeState.volume_spike_shadow
                    }
                };
            }).filter(Boolean);
        }

        function buildVcpAreaData() {
            return (rawData.vcpArea || []).map((area, idx) => {
                if (!Array.isArray(area) || area.length < 2) return area;
                const topOpacity = Math.max(0.04, 0.12 - idx * 0.03);
                const bottomOpacity = Math.max(0.01, 0.03 - idx * 0.006);
                const topColor = idx === 0 ? themeState.vcp_area_top : `rgba(208, 164, 78, ${topOpacity.toFixed(3)})`;
                const bottomColor = idx === 0 ? themeState.vcp_area_bottom : `rgba(208, 164, 78, ${bottomOpacity.toFixed(3)})`;
                return [
                    {
                        ...area[0],
                        itemStyle: {
                            color: _verticalGradient(topColor, bottomColor),
                            borderWidth: 1,
                            borderColor: themeState.vcp_area_border,
                            borderType: idx > 0 ? 'dashed' : 'solid'
                        }
                    },
                    area[1]
                ];
            });
        }

        function buildVcpCurveSeries() {
            const areas = rawData.vcpArea || [];
            const dateIndex = new Map((rawData.dates || []).map((date, idx) => [date, idx]));
            const series = [];
            areas.forEach((area, idx) => {
                if (!Array.isArray(area) || area.length < 2) return;
                const start = area[0] || {};
                const end = area[1] || {};
                const xStart = start.xAxis;
                const xEnd = end.xAxis;
                const yStart = Number(start.yAxis);
                const yEnd = Number(end.yAxis);
                if (!xStart || !xEnd || !Number.isFinite(yStart) || !Number.isFinite(yEnd)) return;

                const startIdx = dateIndex.has(xStart) ? dateIndex.get(xStart) : 0;
                const endIdx = dateIndex.has(xEnd) ? dateIndex.get(xEnd) : startIdx;
                const midIdx = Math.max(0, Math.min(rawData.dates.length - 1, Math.round((startIdx + endIdx) / 2)));
                const xMid = rawData.dates[midIdx] || xStart;
                const high = Math.max(yStart, yEnd);
                const low = Math.min(yStart, yEnd);
                const bend = Math.max((high - low) * 0.07, high * 0.002);
                const common = {
                    type: 'line',
                    xAxisIndex: 0,
                    yAxisIndex: 0,
                    showSymbol: false,
                    smooth: true,
                    silent: true,
                    animation: false,
                    z: 9,
                    emphasis: { disabled: true }
                };
                series.push({
                    ...common,
                    id: 'vcpUpperCurve_' + idx,
                    data: [[xStart, high], [xMid, high - bend], [xEnd, high]],
                    lineStyle: {
                        width: 1.4,
                        color: themeState.vcp_guide,
                        opacity: 0.78
                    }
                });
                series.push({
                    ...common,
                    id: 'vcpLowerCurve_' + idx,
                    data: [[xStart, low], [xMid, low + bend], [xEnd, low]],
                    lineStyle: {
                        width: 1.2,
                        color: themeState.vcp_line_soft,
                        opacity: 0.62
                    }
                });
            });
            return series;
        }

        function buildVcpMarkerData() {
            return (rawData.vcpMarkers || []).map((item) => {
                const idx = Math.round(Number(item.coord && item.coord[0]));
                const y = Number(item.coord && item.coord[1]);
                const category = rawData.dates[idx];
                if (!category || !Number.isFinite(y)) return null;
                return {
                    value: [category, y],
                    symbol: item.symbol || VCP_STAR_SYMBOL,
                    symbolSize: item.symbolSize || 18,
                    symbolOffset: item.symbolOffset || [0, -10],
                    itemStyle: item.itemStyle,
                    label: item.label
                };
            }).filter(Boolean);
        }
