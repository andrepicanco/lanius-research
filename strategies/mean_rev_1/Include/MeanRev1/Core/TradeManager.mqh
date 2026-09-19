//+------------------------------------------------------------------+
//|                                                TradeManager.mqh  |
//|        MeanRev1 - CTrade wrapper: market entries, trailing       |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>
#include "..\Defines.mqh"
#include "Logger.mqh"

class CTradeManager
  {
private:
   CTrade            m_trade;
   string            m_symbol;
   long              m_magic;
   int               m_slippagePoints;
   CLogger          *m_logger;

   double            RoundToTickSize(const double price) const
     {
      const double tickSize = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tickSize <= 0.0)
         return NormalizeDouble(price, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
      return MathRound(price / tickSize) * tickSize;
     }

   int               StopsLevelPoints(void) const
     {
      const int stopsLevel  = (int)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL);
      const int freezeLevel = (int)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_FREEZE_LEVEL);
      return MathMax(stopsLevel, freezeLevel);
     }

   //--- ticket of our own open position on this symbol, or 0 if we have none.
   //--- Walks the position list rather than calling PositionSelect(symbol): on a
   //--- hedging account a symbol can hold several positions at once, and
   //--- PositionSelect() returns whichever comes first - quite possibly another
   //--- EA's. The magic check would then reject it and we would conclude we are
   //--- flat while our own position is still open. Works unchanged on netting.
   ulong             FindPositionTicket(void) const
     {
      const int total = PositionsTotal();
      for(int i = 0; i < total; i++)
        {
         const ulong ticket = PositionGetTicket(i);
         if(ticket == 0)
            continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol)
            continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != m_magic)
            continue;
         return ticket;
        }
      return 0;
     }

public:
                     CTradeManager(void) : m_symbol(_Symbol), m_magic(0), m_slippagePoints(10), m_logger(NULL) {}

   void              Init(const string symbol, const long magic, const int slippagePoints, CLogger *logger)
     {
      m_symbol         = symbol;
      m_magic          = magic;
      m_slippagePoints = slippagePoints;
      m_logger         = logger;

      m_trade.SetExpertMagicNumber(magic);
      m_trade.SetDeviationInPoints(slippagePoints);
      m_trade.SetTypeFillingBySymbol(symbol);
     }

   //--- opens a market position in dir; returns the resulting position ticket
   //--- (>0) on success, 0 on failure. One position per symbol+magic is the
   //--- invariant the whole class relies on - the state machine only calls this
   //--- from STATE_IDLE - but it no longer assumes the *account* is netting.
   ulong             OpenMarketPosition(const ENUM_TRIGGER_DIR dir, double sl, double tp,
                                         const double lots, const string comment)
     {
      const double point      = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      const int    minStopPts = StopsLevelPoints();
      const double ask        = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      const double bid        = SymbolInfoDouble(m_symbol, SYMBOL_BID);
      const double entryPrice = (dir == TRIGGER_BUY) ? ask : bid;

      sl = RoundToTickSize(sl);
      tp = (tp > 0.0) ? RoundToTickSize(tp) : 0.0;

      if(minStopPts > 0)
        {
         const double minDist = (minStopPts + 1) * point;
         if(MathAbs(entryPrice - sl) < minDist)
           {
            if(m_logger != NULL)
               m_logger.Log(LOG_WARN, StringFormat("OpenMarketPosition: SL distance %.5f below broker min %.5f, skipping",
                                                     MathAbs(entryPrice - sl), minDist));
            return 0;
           }
         if(tp > 0.0 && MathAbs(tp - entryPrice) < minDist)
           {
            if(m_logger != NULL)
               m_logger.Log(LOG_WARN, "OpenMarketPosition: TP distance below broker min, skipping");
            return 0;
           }
        }

      bool ok = false;
      if(dir == TRIGGER_BUY)
         ok = m_trade.Buy(lots, m_symbol, ask, sl, tp, comment);
      else
         if(dir == TRIGGER_SELL)
            ok = m_trade.Sell(lots, m_symbol, bid, sl, tp, comment);
         else
            return 0;

      if(!ok)
        {
         if(m_logger != NULL)
            m_logger.Log(LOG_ERROR, StringFormat("OpenMarketPosition failed: retcode=%d desc=%s",
                                                  m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription()));
         return 0;
        }

      const ulong ticket = FindPositionTicket();
      if(ticket == 0)
        {
         if(m_logger != NULL)
            m_logger.Log(LOG_ERROR, "OpenMarketPosition: trade reported success but no position found");
         return 0;
        }
      return ticket;
     }

   //--- actively closes our open position (used by the time-based exit)
   bool              ClosePosition(void)
     {
      const ulong ticket = FindPositionTicket();
      if(ticket == 0)
         return true; // already gone - nothing to do

      // by ticket, not by symbol: PositionClose(symbol) is ambiguous on a hedging
      // account and could close a position belonging to another EA
      if(!m_trade.PositionClose(ticket, (ulong)m_slippagePoints))
        {
         if(m_logger != NULL)
            m_logger.Log(LOG_WARN, StringFormat("ClosePosition: PositionClose failed retcode=%d", m_trade.ResultRetcode()));
         return false;
        }
      return true;
     }

   //--- true if a position with our magic is open on our symbol
   bool              HasOpenPosition(void) const
     {
      return FindPositionTicket() > 0;
     }

   //--- only tightens the stop, never loosens; applies at most once per new bar (called from that context)
   bool              ApplyTrailing(const double atrValue, const double atrMultiplier)
     {
      const ulong ticket = FindPositionTicket();
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         return false;
      if(atrValue <= 0.0 || atrMultiplier <= 0.0)
         return false;

      const long   posType  = PositionGetInteger(POSITION_TYPE);
      const double curSL    = PositionGetDouble(POSITION_SL);
      const double tp       = PositionGetDouble(POSITION_TP);
      const double price    = (posType == POSITION_TYPE_BUY)
                               ? SymbolInfoDouble(m_symbol, SYMBOL_BID)
                               : SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      const double distance = atrValue * atrMultiplier;
      const double point    = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      const int    minStopPts = StopsLevelPoints();
      const double minDist   = (minStopPts + 1) * point;

      double newSL;
      if(posType == POSITION_TYPE_BUY)
        {
         newSL = RoundToTickSize(price - distance);
         if(newSL <= curSL)
            return false; // only move up
         if((price - newSL) < minDist)
            return false;
        }
      else
        {
         newSL = RoundToTickSize(price + distance);
         if(curSL > 0.0 && newSL >= curSL)
            return false; // only move down
         if((newSL - price) < minDist)
            return false;
        }

      if(!m_trade.PositionModify(ticket, newSL, tp))
        {
         if(m_logger != NULL)
            m_logger.Log(LOG_WARN, StringFormat("ApplyTrailing: PositionModify failed retcode=%d", m_trade.ResultRetcode()));
         return false;
        }
      return true;
     }

   string            LastError(void) const
     {
      return StringFormat("retcode=%d desc=%s", m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription());
     }
  };
