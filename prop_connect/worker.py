"""One process per bounded task, in a separately installed library environment."""
import contextlib
import importlib
import importlib.metadata
import io
import json
import math
import os
import re
import sys


def clean(x):
    if hasattr(x,'item'):x=x.item()
    if isinstance(x,float):return x if math.isfinite(x) else None
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if x is None or isinstance(x,(str,bool,int)):return x
    return str(x)


def execute(req):
    integration=req['integration'];action=req['action'];p=req.get('payload',{})
    modules={'quantstats':'quantstats','vectorbt':'vectorbt','pybroker':'pybroker','nautilus':'nautilus_trader','tradingagents':'tradingagents'}
    module=importlib.import_module(modules[integration])
    if action=='probe':
        return {'import_verified':True,'version':getattr(module,'__version__','unknown'),'module':modules[integration],'strategy_validated':False}
    if integration=='quantstats' and action=='metrics':
        import pandas as pd
        days=p['days'];balance=float(p['initial_balance']);returns=[]
        for d in days:
            if balance<=0:raise ValueError('Non-positive balance')
            returns.append(float(d['pnl'])/balance);balance+=float(d['pnl'])
        if len(days)<2:raise ValueError('At least two sessions needed')
        s=pd.Series(returns,index=pd.to_datetime([d['session'] for d in days]))
        return {'max_drawdown':float(module.stats.max_drawdown(s)), 'observed_return':float((s+1).prod()-1),'sessions':len(s),'annualized_sharpe':None,'limitations':'No zero-return missing sessions filled; period-based, not trade-based.'}
    if integration=='vectorbt' and action=='benchmark':
        import pandas as pd
        prices=p.get('prices',[])
        if not 30<=len(prices)<=10000 or any(not math.isfinite(float(v)) or float(v)<=0 for v in prices):raise ValueError('Invalid prices')
        close=pd.Series([float(v) for v in prices],index=pd.date_range('2020-01-01',periods=len(prices),freq='D'))
        fast=module.MA.run(close,5).ma;slow=module.MA.run(close,20).ma
        entries=(fast>slow)&(fast.shift(1)<=slow.shift(1));exits=(fast<slow)&(fast.shift(1)>=slow.shift(1))
        # Signal from completed bar, execution on next bar's price.
        entries=entries.shift(1,fill_value=False);exits=exits.shift(1,fill_value=False)
        pf=module.Portfolio.from_signals(close,entries,exits,size=1,init_cash=10000,fees=.001,slippage=.001)
        return {'technical_benchmark_only':True,'asset_class':'equity','total_return':float(pf.total_return()),'max_drawdown':float(pf.max_drawdown()),'trades':int(pf.trades.count()),'not_a_futures_backtest':True}
    if integration=='pybroker' and action=='benchmark':
        import pandas as pd
        prices=p.get('prices',[])
        if not 30<=len(prices)<=10000 or any(not math.isfinite(float(v)) or float(v)<=0 for v in prices):raise ValueError('Invalid prices')
        symbol='BENCH'
        frame=pd.DataFrame({'symbol':symbol,'date':pd.date_range('2020-01-01',periods=len(prices),freq='D'),'open':prices,'high':prices,'low':prices,'close':prices,'volume':1000})
        def rule(ctx):
            if not ctx.long_pos() and len(ctx.close)>=20:
                ctx.buy_shares=1;ctx.hold_bars=5
        strategy=module.Strategy(frame,frame.date.iloc[0],frame.date.iloc[-1])
        strategy.add_execution(rule,[symbol]);result=strategy.backtest(warmup=20)
        return {'technical_benchmark_only':True,'asset_class':'equity','trades':len(result.trades),'metrics':clean(result.metrics_df.to_dict('records')),'limitations':'SDK smoke benchmark; cost/slippage model not validated; not a futures strategy.'}
    if integration=='tradingagents' and action=='research':
        from datetime import date
        symbol=str(p.get('symbol',''));asof=str(p.get('asof',''))
        if not re.fullmatch(r'[A-Z0-9.^=/\-]{1,32}',symbol):raise ValueError('Invalid symbol')
        date.fromisoformat(asof)
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        config=DEFAULT_CONFIG.copy();config['max_debate_rounds']=1;config['max_risk_discuss_rounds']=1
        for env,key in [('TRADINGAGENTS_LLM_PROVIDER','llm_provider'),('TRADINGAGENTS_DEEP_THINK_LLM','deep_think_llm'),('TRADINGAGENTS_QUICK_THINK_LLM','quick_think_llm')]:
            if os.environ.get(env):config[key]=os.environ[env]
        graph=TradingAgentsGraph(debug=False,config=config)
        state,decision=graph.propagate(symbol,asof)
        return {'symbol':symbol,'asof':asof,'decision_research_only':str(decision),'execution_authorized':False,'futures_coverage_verified':False,'historical_llm_leakage_not_excluded':True}
    raise ValueError('Action not implemented for this library')


if __name__=='__main__':
    try:
        req=json.loads(sys.stdin.read(2_000_000))
        # SDK stdout is not our wire protocol and may contain verbose logs.
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            result=execute(req)
        print(json.dumps({'ok':True,'result':clean(result)},allow_nan=False))
    except Exception as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__}));sys.exit(1)
