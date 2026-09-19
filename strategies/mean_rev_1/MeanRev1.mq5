//+------------------------------------------------------------------+
//|                                                    MeanRev1.mq5  |
//|   Mean-reversion EA: RSI overbought/oversold streak confirmed    |
//|   by SMA short/long trend filter. Enters at market on the close  |
//|   of the signal candle, exits after a fixed number of candles    |
//|   (or SL/TP/trailing if they fire first).                        |
//|   One symbol per instance - attach one chart per instrument.     |
//+------------------------------------------------------------------+
#property copyright "MeanRev1"
#property version   "1.00"
#property strict

// Quoted, path-relative to this .mq5's own folder - resolves correctly
// regardless of where/how this project folder is exposed inside the MT5
// data folder (junction, direct copy, etc.), unlike <angle-bracket>
// includes which only resolve under MQL5\Include\.
#include "Include\MeanRev1\Defines.mqh"
#include "Include\MeanRev1\Core\Logger.mqh"
#include "Include\MeanRev1\Core\BarManager.mqh"
#include "Include\MeanRev1\Core\RiskManager.mqh"
#include "Include\MeanRev1\Core\TradeManager.mqh"
#include "Include\MeanRev1\Signal\MeanRevSignal.mqh"

//--- Timeframe
input ENUM_TIMEFRAMES InpTimeframe        = PERIOD_CURRENT;

//--- Strategy / Signal
input int    InpSMAShortPeriod      = 21;
input int    InpSMALongPeriod       = 88;
input int    InpRSIPeriod           = 2;
input double InpRSIOverboughtLevel  = 80.0;
input double InpRSIOversoldLevel    = 20.0;
input int    InpStreakLength        = 2;

//--- Exit - time-based
input int    InpExitBars            = 6;

//--- Stop Loss / Take Profit
input int    InpSLATRPeriod         = 14;
input double InpSLATRMultiplier     = 2.0;
input double InpTP_RMultiple        = 2.0;

//--- Trailing (ATR, optional)
input bool   InpUseTrailing         = false;
input int    InpTrailATRPeriod      = 14;
input double InpTrailATRMultiplier  = 2.0;

//--- Position sizing
input bool   InpUseFixedLot         = true;
input double InpFixedLot            = 0.10;
input double InpRiskPercent         = 1.0;

//--- Trade management
input long   InpMagicNumber         = 552601;
input string InpTradeComment        = "MeanRev1";
input int    InpSlippagePoints      = 10;

//--- Optional filters (off by default)
// 0 = disabled
input int    InpMaxSpreadPoints        = 0; 
input bool   InpUseTradingHoursFilter  = false;
// server/broker time
input int    InpStartHour              = 0;
// server/broker time
input int    InpEndHour                = 23;

//--- Diagnostics
input ENUM_LOG_LEVEL InpLogLevel = LOG_INFO;

//--- Globals
CLogger        g_logger;
CBarManager    g_bar;
CRiskManager   g_risk;
CTradeManager  g_trade;
CMeanRevSignal g_signal;

ENUM_TIMEFRAMES g_workTF;
int             hSLATR    = INVALID_HANDLE;
int             hTrailATR = INVALID_HANDLE;

ENUM_EA_STATE g_state          = STATE_IDLE;
ulong         g_positionTicket = 0;
int           g_barsInPosition = 0;

//+------------------------------------------------------------------+
bool ValidateInputs(void)
  {
   if(InpSMAShortPeriod <= 0 || InpSMALongPeriod <= 0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: SMA periods must be > 0");
      return false;
     }
   if(InpSMAShortPeriod >= InpSMALongPeriod)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpSMAShortPeriod must be < InpSMALongPeriod");
      return false;
     }
   if(InpRSIPeriod <= 0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpRSIPeriod must be > 0");
      return false;
     }
   if(InpRSIOverboughtLevel <= InpRSIOversoldLevel || InpRSIOverboughtLevel > 100.0 || InpRSIOversoldLevel < 0.0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpRSIOverboughtLevel/InpRSIOversoldLevel must be in [0,100] with Overbought > Oversold");
      return false;
     }
   if(InpStreakLength <= 0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpStreakLength must be > 0");
      return false;
     }
   if(InpExitBars <= 0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpExitBars must be > 0");
      return false;
     }
   if(InpSLATRPeriod <= 0 || InpSLATRMultiplier <= 0.0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: SL ATR period/multiplier must be > 0");
      return false;
     }
   if(InpTP_RMultiple <= 0.0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpTP_RMultiple must be > 0");
      return false;
     }
   if(InpUseTrailing && (InpTrailATRPeriod <= 0 || InpTrailATRMultiplier <= 0.0))
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: trailing ATR period/multiplier must be > 0 when trailing is enabled");
      return false;
     }
   if(!InpUseFixedLot && (InpRiskPercent <= 0.0 || InpRiskPercent > 20.0))
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpRiskPercent must be in (0, 20]");
      return false;
     }
   if(InpUseFixedLot && InpFixedLot <= 0.0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpFixedLot must be > 0");
      return false;
     }
   if(InpMaxSpreadPoints < 0)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: InpMaxSpreadPoints must be >= 0");
      return false;
     }
   if(InpStartHour < 0 || InpStartHour > 23 || InpEndHour < 0 || InpEndHour > 23)
     {
      g_logger.Log(LOG_ERROR, "ValidateInputs: hour filter inputs must be in [0,23]");
      return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
int OnInit(void)
  {
   g_logger.Init(InpLogLevel);

   if(!ValidateInputs())
      return INIT_PARAMETERS_INCORRECT;

   g_workTF = (InpTimeframe == PERIOD_CURRENT) ? _Period : InpTimeframe;

   if(!g_signal.Init(_Symbol, g_workTF, InpSMAShortPeriod, InpSMALongPeriod, InpRSIPeriod,
                      InpRSIOverboughtLevel, InpRSIOversoldLevel, InpStreakLength, GetPointer(g_logger)))
      return INIT_FAILED;

   hSLATR = iATR(_Symbol, g_workTF, InpSLATRPeriod);
   if(hSLATR == INVALID_HANDLE)
     {
      g_logger.Log(LOG_ERROR, "OnInit: failed to create SL ATR handle");
      return INIT_FAILED;
     }

   if(InpUseTrailing)
     {
      hTrailATR = iATR(_Symbol, g_workTF, InpTrailATRPeriod);
      if(hTrailATR == INVALID_HANDLE)
        {
         g_logger.Log(LOG_ERROR, "OnInit: failed to create trailing ATR handle");
         return INIT_FAILED;
        }
     }

   g_trade.Init(_Symbol, InpMagicNumber, InpSlippagePoints, GetPointer(g_logger));
   g_risk.Init(_Symbol, InpUseFixedLot, InpFixedLot, InpRiskPercent, GetPointer(g_logger));

   g_bar.Reset();
   g_state          = STATE_IDLE;
   g_positionTicket = 0;
   g_barsInPosition = 0;

   g_logger.Log(LOG_INFO, StringFormat(
      "OnInit: ready. TF=%s SMA=%d/%d RSI=%d OB=%.1f OS=%.1f Streak=%d ExitBars=%d SLATRx=%.2f TP_R=%.2f",
      EnumToString(g_workTF), InpSMAShortPeriod, InpSMALongPeriod, InpRSIPeriod,
      InpRSIOverboughtLevel, InpRSIOversoldLevel, InpStreakLength, InpExitBars, InpSLATRMultiplier, InpTP_RMultiple));
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   g_signal.Deinit();
   if(hSLATR != INVALID_HANDLE)
      IndicatorRelease(hSLATR);
   // iATR() returns the same handle for identical symbol/tf/period, so guard
   // against double-releasing when InpSLATRPeriod == InpTrailATRPeriod.
   if(hTrailATR != INVALID_HANDLE && hTrailATR != hSLATR)
      IndicatorRelease(hTrailATR);
  }

//+------------------------------------------------------------------+
bool WithinTradingHours(void)
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(InpStartHour <= InpEndHour)
      return dt.hour >= InpStartHour && dt.hour <= InpEndHour;
   // wraps past midnight (e.g. Start=22, End=6)
   return dt.hour >= InpStartHour || dt.hour <= InpEndHour;
  }

//+------------------------------------------------------------------+
int CurrentSpreadPoints(void)
  {
   const double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   const double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(point <= 0.0)
      return 0;
   return (int)MathRound((ask - bid) / point);
  }

//+------------------------------------------------------------------+
void RefreshState(void)
  {
   if(g_trade.HasOpenPosition())
     {
      if(g_state != STATE_IN_POSITION)
         g_logger.Log(LOG_WARN, "RefreshState: adopting an already-open position (EA reload?) - bar count reset to 0");
      g_state = STATE_IN_POSITION;
      return;
     }

   if(g_state == STATE_IN_POSITION)
      g_logger.Log(LOG_INFO, "RefreshState: tracked position is gone (SL/TP/manual close) - resetting to IDLE");

   g_state          = STATE_IDLE;
   g_positionTicket = 0;
   g_barsInPosition = 0;
  }

//+------------------------------------------------------------------+
void HandleIdleState(void)
  {
   if(InpUseTradingHoursFilter && !WithinTradingHours())
      return;

   ENUM_TRIGGER_DIR dir;
   datetime signalBarTime;

   if(!g_signal.CheckForSignal(dir, signalBarTime))
      return;

   if(InpMaxSpreadPoints > 0 && CurrentSpreadPoints() > InpMaxSpreadPoints)
     {
      g_logger.Log(LOG_INFO, "HandleIdleState: signal skipped, spread filter");
      return;
     }

   double atrBuf[];
   if(CopyBuffer(hSLATR, 0, 1, 1, atrBuf) != 1)
     {
      g_logger.Log(LOG_WARN, "HandleIdleState: SL ATR not ready");
      return;
     }
   const double atrValue = atrBuf[0];
   if(atrValue <= 0.0)
      return;

   const double slDistance = atrValue * InpSLATRMultiplier;
   const double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   const double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   const double entryPrice = (dir == TRIGGER_BUY) ? ask : bid;

   const double slPrice = (dir == TRIGGER_BUY) ? entryPrice - slDistance : entryPrice + slDistance;
   const double tpPrice = (dir == TRIGGER_BUY)
                           ? entryPrice + InpTP_RMultiple * slDistance
                           : entryPrice - InpTP_RMultiple * slDistance;

   const double lots = g_risk.CalculateLotSize(slDistance);

   const ulong ticket = g_trade.OpenMarketPosition(dir, slPrice, tpPrice, lots, InpTradeComment);
   if(ticket > 0)
     {
      g_state          = STATE_IN_POSITION;
      g_positionTicket = ticket;
      g_barsInPosition = 0;
      g_logger.Log(LOG_INFO, StringFormat("HandleIdleState: opened %s @ %.5f SL=%.5f TP=%.5f lots=%.2f ticket=%I64u",
                                           (dir == TRIGGER_BUY ? "BUY" : "SELL"), entryPrice, slPrice, tpPrice, lots, ticket));
     }
   else
      g_logger.Log(LOG_ERROR, "HandleIdleState: market order failed - " + g_trade.LastError());
  }

//+------------------------------------------------------------------+
void HandleInPositionState(void)
  {
   g_barsInPosition++;

   if(g_barsInPosition >= InpExitBars)
     {
      if(g_trade.ClosePosition())
        {
         g_logger.Log(LOG_INFO, StringFormat("HandleInPositionState: time-based exit after %d bars", g_barsInPosition));
         g_state          = STATE_IDLE;
         g_positionTicket = 0;
         g_barsInPosition = 0;
        }
      else
         g_logger.Log(LOG_ERROR, "HandleInPositionState: time-based close failed - " + g_trade.LastError());
      return;
     }

   if(InpUseTrailing && hTrailATR != INVALID_HANDLE)
     {
      double atrBuf[];
      if(CopyBuffer(hTrailATR, 0, 1, 1, atrBuf) == 1)
         g_trade.ApplyTrailing(atrBuf[0], InpTrailATRMultiplier);
     }
  }

//+------------------------------------------------------------------+
void HandleNewBar(void)
  {
   RefreshState();

   switch(g_state)
     {
      case STATE_IN_POSITION:
         HandleInPositionState();
         break;
      case STATE_IDLE:
         HandleIdleState();
         break;
     }
  }

//+------------------------------------------------------------------+
void OnTick(void)
  {
   if(!g_bar.IsNewBar(_Symbol, g_workTF))
      return;

   HandleNewBar();
  }
//+------------------------------------------------------------------+
