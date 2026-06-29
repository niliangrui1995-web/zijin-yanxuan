        function buildOption() {
            const data = splitData(rawData);
            return {
                animation: false,
                stateAnimation: { duration: 0 },
                backgroundColor: _chartBackgroundColor(),
                legend: {
                    show: false
                },
                axisPointer: {
                    link: [{ xAxisIndex: 'all' }],
                    lineStyle: {
                        color: themeState.crosshair_line,
                        width: 1.2,
                        type: 'dashed',
                        opacity: 0.92
                    },
                    crossStyle: {
                        color: themeState.crosshair_line,
                        width: 1.2,
                        opacity: 0.92
                    },
                    label: {
                        backgroundColor: themeState.pointer_bg,
                        color: themeState.tooltip_text,
                        fontFamily: themeState.mono_font_family,
                        borderRadius: 4,
                        padding: [3, 6],
                        shadowBlur: 8,
                        shadowColor: themeState.crosshair_line,
                        shadowOffsetY: 0
                    }
                },
                tooltip: {
                    trigger: 'axis',
                    showContent: false,
                    axisPointer: {
                        type: 'cross',
                        lineStyle: { color: themeState.crosshair_line, width: 1.2, opacity: 0.92 },
                        crossStyle: { color: themeState.crosshair_line, width: 1.2, opacity: 0.92 }
                    },
                    backgroundColor: themeState.tooltip_bg,
                    borderWidth: 0,
                    textStyle: { color: themeState.tooltip_text, fontFamily: themeState.mono_font_family }
                },
                grid: [
                    { left: KLINE_GRID_LEFT, right: KLINE_GRID_RIGHT, top: 18, height: '56%' },
                    { left: KLINE_GRID_LEFT, right: KLINE_GRID_RIGHT, top: '67%', height: '11%' },
                    { left: KLINE_GRID_LEFT, right: KLINE_GRID_RIGHT, top: '81%', height: '12%' }
                ],
                xAxis: [
                    {
                        type: 'category',
                        data: data.categoryData,
                        scale: true,
                        boundaryGap: false,
                        axisLine: { lineStyle: { color: themeState.axis_line } },
                        axisLabel: { color: themeState.axis_label, fontFamily: themeState.mono_font_family },
                        axisPointer: { label: { show: false } },
                        splitLine: { show: false },
                        min: 'dataMin',
                        max: 'dataMax'
                    },
                    {
                        type: 'category',
                        gridIndex: 1,
                        data: data.categoryData,
                        scale: true,
                        boundaryGap: false,
                        axisLine: { lineStyle: { color: themeState.axis_line } },
                        axisLabel: { show: false },
                        axisPointer: { label: { show: false } },
                        axisTick: { show: false },
                        splitLine: { show: false },
                        min: 'dataMin',
                        max: 'dataMax'
                    },
                    {
                        type: 'category',
                        gridIndex: 2,
                        data: data.categoryData,
                        scale: true,
                        boundaryGap: false,
                        axisLine: { lineStyle: { color: themeState.axis_line } },
                        axisLabel: { color: themeState.axis_label, fontFamily: themeState.mono_font_family },
                        min: 'dataMin',
                        max: 'dataMax'
                    }
                ],
                yAxis: [
                    {
                        scale: true,
                        splitArea: { show: false },
                        splitLine: { lineStyle: { color: themeState.grid_line, type: [4, 4] } },
                        axisLine: { lineStyle: { color: themeState.axis_line } },
                        axisLabel: { color: themeState.axis_label, fontFamily: themeState.mono_font_family }
                    },
                    {
                        scale: true,
                        gridIndex: 1,
                        splitNumber: 2,
                        axisLabel: { color: themeState.axis_label, fontFamily: themeState.mono_font_family, formatter: _formatVolumeWan },
                        axisLine: { lineStyle: { color: themeState.axis_line } },
                        splitLine: { show: false }
                    },
                    {
                        scale: true,
                        gridIndex: 2,
                        splitNumber: 2,
                        axisLabel: { color: themeState.axis_label, fontFamily: themeState.mono_font_family },
                        axisLine: { lineStyle: { color: themeState.axis_line } },
                        splitLine: { show: false }
                    }
                ],
                dataZoom: [
                    {
                        type: 'inside',
                        xAxisIndex: [0, 1, 2],
                        zoomOnMouseWheel: false,
                        moveOnMouseWheel: false,
                        moveOnMouseMove: true,
                        preventDefaultMouseMove: true,
                        filterMode: 'none',
                        throttle: 0,
                        minSpan: _minZoomSpan(),
                        start: 55,
                        end: 100
                    },
                    {
                        show: false,
                        xAxisIndex: [0, 1, 2],
                        type: 'slider',
                        top: '94%',
                        filterMode: 'none',
                        minSpan: _minZoomSpan(),
                        start: 55,
                        end: 100,
                        backgroundColor: themeState.datazoom_bg,
                        fillerColor: themeState.datazoom_fill,
                        borderColor: themeState.border,
                        handleStyle: {
                            color: themeState.datazoom_handle,
                            borderColor: themeState.datazoom_handle
                        },
                        textStyle: {
                            color: themeState.axis_label,
                            fontFamily: themeState.mono_font_family
                        }
                    }
                ],
                series: [
                    {
                        name: '日K',
                        id: 'kline',
                        type: 'candlestick',
                        data: data.values,
                        barMinWidth: 2,
                        barMaxWidth: 18,
                        itemStyle: {
                            color: upColor,
                            color0: downColor,
                            borderColor: upColor,
                            borderColor0: downColor,
                            borderWidth: 1
                        },
                        markLine: rawData.vcpLines ? {
                            symbol: ['none', 'none'],
                            silent: true,
                            animation: false,
                            label: { show: false },
                            lineStyle: {
                                color: themeState.vcp_line,
                                width: 1,
                                type: 'dashed',
                                opacity: 0.78
                            },
                            data: rawData.vcpLines
                        } : undefined,
                        markArea: rawData.vcpArea ? {
                            silent: true,
                            animation: false,
                            itemStyle: {
                                color: themeState.vcp_area
                            },
                            data: buildVcpAreaData()
                        } : undefined
                    },
                    ...buildVcpCurveSeries(),
                    {
                        name: 'VCP Breakout',
                        id: 'vcpBreakout',
                        type: 'effectScatter',
                        coordinateSystem: 'cartesian2d',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: buildVcpMarkerData(),
                        clip: true,
                        z: 12,
                        silent: true,
                        showEffectOn: 'render',
                        rippleEffect: {
                            period: 4,
                            scale: 3.2,
                            brushType: 'stroke',
                            color: themeState.vcp_star
                        },
                        animation: false,
                        animationDuration: 0,
                        animationDurationUpdate: 0,
                        emphasis: { disabled: true },
                        itemStyle: {
                            color: themeState.vcp_star,
                            borderColor: themeState.vcp_area_border,
                            borderWidth: 1,
                            shadowBlur: 14,
                            shadowColor: themeState.vcp_star
                        }
                    },
                    {
                        id: 'earningsDay',
                        name: '业绩日',
                        type: 'scatter',
                        coordinateSystem: 'cartesian2d',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: buildEarningsMarkerData(),
                        symbol: 'triangle',
                        silent: false,
                        clip: false,
                        z: 13,
                        animation: false,
                        animationDuration: 0,
                        animationDurationUpdate: 0,
                        emphasis: { disabled: true },
                        tooltip: {
                            show: true,
                            showContent: true,
                            trigger: 'item',
                            confine: true,
                            backgroundColor: themeState.tooltip_bg,
                            borderColor: themeState.earnings_marker_border,
                            borderWidth: 1,
                            padding: [8, 10],
                            textStyle: {
                                color: themeState.tooltip_text,
                                fontFamily: themeState.mono_font_family,
                                fontSize: 12,
                                lineHeight: 18
                            },
                            formatter: function (params) {
                                const data = params.data || {};
                                const dateText = _escapeHtml(data.sourceDate || data.markerDate || '');
                                const summary = _escapeHtml(data.summary || '');
                                const qoqText = _escapeHtml(data.qoqText || '-');
                                const yoyText = _escapeHtml(data.yoyText || '-');
                                const summaryHtml = summary
                                    ? '<div style="margin-top:4px; color:' + themeState.axis_label + ';">' + summary + '</div>'
                                    : '';
                                return ''
                                    + '<div style="min-width:150px;">'
                                    + '<div style="font-weight:700; color:' + themeState.earnings_marker + ';">业绩日 ' + dateText + '</div>'
                                    + '<div style="margin-top:6px;">环比：<span style="font-weight:700;">' + qoqText + '</span></div>'
                                    + '<div>同比：<span style="font-weight:700;">' + yoyText + '</span></div>'
                                    + summaryHtml
                                    + '</div>';
                            }
                        },
                        itemStyle: {
                            color: themeState.earnings_marker,
                            borderColor: themeState.bg_canvas,
                            borderWidth: 1,
                            shadowBlur: 10,
                            shadowColor: themeState.earnings_marker
                        },
                        label: {
                            show: true,
                            formatter: function (params) {
                                return (params.data && params.data.labelText) || '业绩日';
                            },
                            position: 'bottom',
                            distance: 4,
                            padding: [2, 6],
                            borderRadius: 6,
                            backgroundColor: themeState.earnings_marker_bg,
                            borderColor: themeState.earnings_marker_border,
                            borderWidth: 1,
                            color: themeState.earnings_marker,
                            fontSize: 10,
                            fontWeight: 700
                        }
                    },
                    {
                        id: 'pointerClose',
                        name: 'Pointer Close',
                        type: 'scatter',
                        coordinateSystem: 'cartesian2d',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: [],
                        symbol: 'circle',
                        symbolSize: 7,
                        silent: true,
                        z: 16,
                        itemStyle: {
                            color: themeState.crosshair_line,
                            borderColor: themeState.bg_canvas,
                            borderWidth: 1,
                            shadowBlur: 10,
                            shadowColor: themeState.crosshair_line
                        }
                    },
                    {
                        id: 'tradeMarkers',
                        name: '交易点',
                        type: 'scatter',
                        coordinateSystem: 'cartesian2d',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: buildTradeMarkerData(),
                        symbol: 'roundRect',
                        silent: false,
                        clip: false,
                        z: 15,
                        animation: false,
                        animationDuration: 0,
                        animationDurationUpdate: 0,
                        tooltip: {
                            show: true,
                            showContent: true,
                            trigger: 'item',
                            confine: true,
                            enterable: false,
                            alwaysShowContent: false,
                            hideDelay: 0,
                            backgroundColor: themeState.tooltip_bg,
                            borderColor: themeState.trade_marker_border,
                            borderWidth: 1,
                            padding: [9, 11],
                            textStyle: {
                                color: themeState.tooltip_text,
                                fontFamily: themeState.mono_font_family,
                                fontSize: 12,
                                lineHeight: 18
                            },
                            formatter: function (params) {
                                const data = params.data || {};
                                const label = _escapeHtml(data.labelText || '');
                                const dateText = _escapeHtml(data.tradeDate || '');
                                const sideText = _escapeHtml(data.sideText || '');
                                const titleColor = data.labelText === 'T'
                                    ? themeState.trade_t
                                    : (data.side === 'sell' ? themeState.trade_sell : themeState.trade_buy);
                                return ''
                                    + '<div style="min-width:188px;">'
                                    + '<div style="font-weight:800; color:' + titleColor + ';">' + label + '点 · ' + sideText + ' · ' + dateText + '</div>'
                                    + '<div style="margin-top:6px;">成交股数：<span style="font-weight:700;">' + _formatTradeNumber(data.quantity, 0) + '</span></div>'
                                    + '<div>成交均价：<span style="font-weight:700;">' + _formatTradeNumber(data.price, 3) + '</span></div>'
                                    + '<div>成交金额：<span style="font-weight:700;">' + _formatTradeNumber(data.amount, 2) + '</span></div>'
                                    + '<div style="margin-top:4px; color:' + themeState.axis_label + ';">手续费 '
                                    + _formatTradeNumber(data.fee, 2)
                                    + ' / 印花税 ' + _formatTradeNumber(data.stampTax, 2)
                                    + ' / 杂费 ' + _formatTradeNumber(data.otherFee, 2)
                                    + '</div>'
                                    + '</div>';
                            }
                        },
                        itemStyle: {
                            borderColor: themeState.bg_canvas,
                            borderWidth: 0.8
                        },
                        label: {
                            show: true,
                            formatter: function (params) {
                                return (params.data && params.data.labelText) || '';
                            },
                            position: 'inside',
                            color: '#FFFFFF',
                            fontSize: 9,
                            fontWeight: 800
                        },
                        emphasis: {
                            scale: 1.08
                        }
                    },
                    {
                        id: 'ma10',
                        name: 'MA10',
                        type: 'line',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: rawData.ma10,
                        smooth: false,
                        animation: false,
                        showSymbol: false,
                        lineStyle: _maLineStyle('ma10', 1, 0.72)
                    },
                    {
                        id: 'ma20',
                        name: 'MA20',
                        type: 'line',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: rawData.ma20,
                        smooth: false,
                        animation: false,
                        showSymbol: false,
                        lineStyle: _maLineStyle('ma20', 1, 0.76)
                    },
                    {
                        id: 'ma50',
                        name: 'MA50',
                        type: 'line',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: rawData.ma50,
                        smooth: false,
                        animation: false,
                        showSymbol: false,
                        lineStyle: _maLineStyle('ma50', 1.7, 0.90)
                    },
                    {
                        id: 'ma150',
                        name: 'MA150',
                        type: 'line',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: rawData.ma150,
                        smooth: false,
                        animation: false,
                        showSymbol: false,
                        lineStyle: _maLineStyle('ma150', 1.2, 0.46, 'dashed')
                    },
                    {
                        id: 'ma200',
                        name: 'MA200',
                        type: 'line',
                        xAxisIndex: 0,
                        yAxisIndex: 0,
                        data: rawData.ma200,
                        smooth: false,
                        animation: false,
                        showSymbol: false,
                        lineStyle: _maLineStyle('ma200', 1.2, 0.46, 'dashed')
                    },
                    {
                        id: 'volume',
                        name: 'Volume',
                        type: 'bar',
                        xAxisIndex: 1,
                        yAxisIndex: 1,
                        barMinWidth: 2,
                        barMaxWidth: 18,
                        barCategoryGap: '42%',
                        data: buildVolumeData('normal')
                    },
                    {
                        id: 'volumeDry',
                        name: 'Dry Volume',
                        type: 'bar',
                        xAxisIndex: 1,
                        yAxisIndex: 1,
                        barMinWidth: 1,
                        barMaxWidth: 9,
                        barGap: '-100%',
                        data: buildVolumeData('dry'),
                        z: 4
                    },
                    {
                        id: 'volumeSpikeParticles',
                        name: 'Spike Volume Particles',
                        type: 'effectScatter',
                        coordinateSystem: 'cartesian2d',
                        xAxisIndex: 1,
                        yAxisIndex: 1,
                        data: buildVolumeSpikeParticles(),
                        symbol: 'circle',
                        showEffectOn: 'render',
                        rippleEffect: {
                            period: 3.2,
                            scale: 2.6,
                            brushType: 'stroke',
                            color: themeState.volume_spike_top
                        },
                        silent: true,
                        z: 9
                    },
                    {
                        id: 'volMa20',
                        name: 'VOL-MA20',
                        type: 'line',
                        xAxisIndex: 1,
                        yAxisIndex: 1,
                        data: rawData.volMa20,
                        showSymbol: false,
                        smooth: true,
                        lineStyle: { width: 1.1, color: themeState.vol_ma20 }
                    },
                    {
                        id: 'macd',
                        name: 'MACD',
                        type: 'bar',
                        xAxisIndex: 2,
                        yAxisIndex: 2,
                        data: rawData.macd
                    },
                    {
                        id: 'diff',
                        name: 'DIFF',
                        type: 'line',
                        xAxisIndex: 2,
                        yAxisIndex: 2,
                        data: rawData.diff,
                        showSymbol: false,
                        smooth: true,
                        lineStyle: { width: 1.1, color: themeState.macd_diff }
                    },
                    {
                        id: 'dea',
                        name: 'DEA',
                        type: 'line',
                        xAxisIndex: 2,
                        yAxisIndex: 2,
                        data: rawData.dea,
                        showSymbol: false,
                        smooth: true,
                        lineStyle: { width: 1.1, color: themeState.macd_dea }
                    }
                ]
            };
        }
