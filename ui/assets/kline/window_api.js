        _applyMarketChrome();
        chart.setOption(buildOption());
        _installSmoothWheelZoom();
        _updateToolbar(rawData.dates.length - 1, false);

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
            chart.resize();
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
            chart.resize();
            return true;
        };

        window.replaceKlineData = function (payload) {
            if (!payload || !payload.data) return false;
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
            chart.setOption(nextOption, { notMerge: false, lazyUpdate: true, replaceMerge: ['series'] });
            chart.resize();
            lastToolbarIdx = -1;
            _updateToolbar(rawData.dates.length - 1, false);
            _clearPointerCloseMarker();
            return true;
        };

        window.updateLastBar = function (payload) {
            if (!payload || !payload.date) return;

            const lastIndex = rawData.dates.length - 1;
            const isSameDay = lastIndex >= 0 && rawData.dates[lastIndex] === payload.date;
            const klineEntry = [payload.open, payload.close, payload.low, payload.high];
            const volEntry = {
                value: payload.vol || 0
            };

            if (isSameDay) {
                rawData.klines[lastIndex] = klineEntry;
                rawData.vols[lastIndex] = volEntry;
            } else {
                rawData.dates.push(payload.date);
                rawData.klines.push(klineEntry);
                rawData.vols.push(volEntry);
            }

            chart.setOption({
                xAxis: [
                    { data: rawData.dates },
                    { data: rawData.dates },
                    { data: rawData.dates }
                ],
                series: [
                    { id: 'kline', data: rawData.klines },
                    { id: 'ma10', data: rawData.ma10 },
                    { id: 'ma20', data: rawData.ma20 },
                    { id: 'ma50', data: rawData.ma50 },
                    { id: 'ma150', data: rawData.ma150 },
                    { id: 'ma200', data: rawData.ma200 },
                    { id: 'volume', data: buildVolumeData('normal') },
                    { id: 'volumeDry', data: buildVolumeData('dry') },
                    { id: 'volumeSpikeParticles', data: buildVolumeSpikeParticles() },
                    { id: 'volMa20', data: rawData.volMa20 },
                    { id: 'macd', data: rawData.macd },
                    { id: 'diff', data: rawData.diff },
                    { id: 'dea', data: rawData.dea }
                ]
            }, false, true);
            _updateToolbar(rawData.dates.length - 1, false);
        };

        window.addEventListener('resize', function () {
            chart.resize();
        });
