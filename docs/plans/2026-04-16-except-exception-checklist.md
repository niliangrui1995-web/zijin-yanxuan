# 2026-04-16 高优先级 except Exception 整改清单

> 目标：对业务主链路中的宽泛异常捕获逐项收紧，清理完成后复核剩余数量。
>
> 复核结果：已完成。
> 对账命令：
> `rg -n "except Exception" core\rps_precomputer.py core\sector_rps_helper.py vcp\engine.py vcp\engine_external.py vcp\polars_engine.py vcp\sector.py vcp\data_provider_local.py ui\startup_loader.py ui\workers\rt_scan_worker.py ui\kline_window_runtime.py ui\viewmodels\watchlist_vm.py ui\tabs\na_daily_tab.py ui\workers\scan_worker.py ui\tabs\foreign_block_trade_tab.py ui\tabs\rt_monitor_tab.py ui\tabs\watchlist_tab.py ui\tabs\scan_tab.py ui\tabs\asian_market_tab.py`
> 返回：`0` 条。

## ui\startup_loader.py
- [x] L70  except Exception:
- [x] L103  except Exception as e:
- [x] L118  except Exception as e:
- [x] L175  except Exception as e:
- [x] L205  except Exception as e:
- [x] L214  except Exception as e:
- [x] L234  except Exception as e:

## vcp\engine_external.py
- [x] L77  except Exception:
- [x] L116  except Exception as exc:
- [x] L141  except Exception as exc:
- [x] L165  except Exception as exc:
- [x] L244  except Exception:
- [x] L265  except Exception as exc:
- [x] L276  except Exception as exc:
- [x] L313  except Exception as exc:

## ui\workers\rt_scan_worker.py
- [x] L86  except Exception as e:
- [x] L137  except Exception as _e:
- [x] L165  except Exception as e:
- [x] L170  except Exception as e:
- [x] L270  except Exception as _e:
- [x] L366  except Exception as _e:
- [x] L432  except Exception as e:
- [x] L451  except Exception as e:

## ui\tabs\asian_market_tab.py
- [x] L227  except Exception:
- [x] L232  except Exception as e:
- [x] L275  except Exception as e:
- [x] L409  except Exception as e:
- [x] L435  except Exception as e:
- [x] L524  except Exception as fetch_exc:
- [x] L576  except Exception as exc:
- [x] L602  except Exception as exc:

## core\rps_precomputer.py
- [x] L67  except Exception as e:
- [x] L85  except Exception as e:
- [x] L117  except Exception as e:
- [x] L120  except Exception as e:
- [x] L160  except Exception as e:
- [x] L191  except Exception as e:
- [x] L201  except Exception as e:
- [x] L212  except Exception as e:

## vcp\engine.py
- [x] L204  except Exception as e:
- [x] L219  except Exception as e:
- [x] L246  except Exception as e:
- [x] L397  except Exception as _e:
- [x] L720  except Exception as _e:
- [x] L729  except Exception as _e:
- [x] L788  except Exception as e:
- [x] L815  except Exception as e:

## vcp\data_provider_local.py
- [x] L70  except Exception as exc:
- [x] L100  except Exception as exc:
- [x] L103  except Exception as exc:
- [x] L195  except Exception as exc:
- [x] L246  except Exception:

## ui\kline_window_runtime.py
- [x] L184  except Exception as exc:
- [x] L261  except Exception as exc:

## ui\tabs\na_daily_tab.py
- [x] L198  except Exception as e:
- [x] L205  except Exception as _e:

## ui\workers\scan_worker.py
- [x] L132  except Exception as e:
- [x] L211  except Exception as e:
- [x] L222  except Exception as e:

## ui\tabs\foreign_block_trade_tab.py
- [x] L369  except Exception as e:
- [x] L416  except Exception as e:

## ui\tabs\rt_monitor_tab.py
- [x] L274  except Exception as e:
- [x] L362  except Exception as e:
- [x] L428  except Exception:
- [x] L471  except Exception as e:

## ui\viewmodels\watchlist_vm.py
- [x] L59  except Exception as e:
- [x] L70  except Exception as e:

## ui\tabs\watchlist_tab.py
- [x] L373  except Exception as e:
- [x] L447  except Exception as e:
- [x] L476  except Exception as e:
- [x] L515  except Exception as _e:
- [x] L523  except Exception as e:
- [x] L660  except Exception as e:

## vcp\polars_engine.py
- [x] L159  except Exception as _e:
- [x] L210  except Exception as e:
- [x] L227  except Exception as e:
- [x] L412  except Exception as _e:
- [x] L488  except Exception as e:
- [x] L557  except Exception as _e:

## vcp\sector.py
- [x] L107  except Exception as e:
- [x] L135  except Exception as e:
- [x] L155  except Exception as e:
- [x] L166  except Exception as e:
- [x] L205  except Exception as e:
- [x] L250  except Exception as e:
- [x] L297  except Exception as _e:

## core\sector_rps_helper.py
- [x] L78  except Exception as exc:
- [x] L96  except Exception as exc:
- [x] L112  except Exception as exc:
- [x] L130  except Exception as exc:

## ui\tabs\scan_tab.py
- [x] L154  except Exception:
- [x] L162  except Exception:
- [x] L172  except Exception:
- [x] L556  except Exception as e:
- [x] L621  except Exception as e:
- [x] L650  except Exception as e:
- [x] L696  except Exception as e:

---

# 2026-04-16 中优先级逐步收紧清单

> 目标：继续收紧“可保留但需逐步收口”的宽泛异常捕获，并把 `core/ui/vcp` 范围剩余尾项一次清掉，避免半改半留。
>
> 复核结果：已完成。
> 对账命令：
> `rg -n "except Exception" core ui vcp`
> 返回：`0` 条。

## 本轮完成范围
- [x] `ui\workers\lhb_worker.py` 8 处
- [x] `core\lhb_pool_manager.py` 5 处
- [x] `ui\tabs\lhb_tab.py` 3 处
- [x] `vcp\fetchers\asian_kline_fetcher.py` 5 处
- [x] `ui\tabs\asian_market_workers.py` 4 处
- [x] `ui\tabs\asian_market_runtime.py` 1 处
- [x] `ui\tabs\asian_market_meta.py` 1 处
- [x] `vcp\data_provider_quotes.py` 3 处
- [x] `core\data_store.py` 1 处
- [x] `core\quote_dispatcher.py` 1 处
- [x] `core\task_manager.py` 2 处
- [x] `core\runtime_env.py` 1 处
- [x] `vcp\utils.py` 3 处
- [x] `ui\components\main_window_shell.py` 1 处
- [x] `ui\tabs\earnings_tab.py` 1 处
- [x] `ui\kline_window_header.py` 2 处
- [x] `ui\components\kline_window_manager.py` 2 处
- [x] `ui\models\table_models.py` 1 处
- [x] `ui\models\table_model_helpers.py` 1 处
- [x] `ui\tabs\log_tab.py` 4 处

## 本轮验证
- [x] `ruff check` 通过
- [x] `python -m py_compile` 通过
- [x] `uv run pytest -q tests\test_startup_loader.py tests\test_asian_market_tab.py tests\test_foreign_block_trade_helpers.py` 通过（`21 passed`）
- [x] `uv run pytest -q tests\test_runtime_env.py tests\test_task_manager.py tests\test_quote_dispatcher.py tests\test_data_store.py tests\test_lhb_tab.py tests\test_lhb_worker_logging.py tests\test_lhb_foreign_display.py tests\test_lhb_pool_manager.py tests\test_asian_kline_fetcher.py tests\test_log_tab.py` 通过（`32 passed`）
