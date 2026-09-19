//+------------------------------------------------------------------+
//|                                               MeanRevSignal.mqh  |
//|   MeanRev1 - RSI overbought/oversold streak + SMA trend filter   |
//+------------------------------------------------------------------+
#property strict

#include "..\Defines.mqh"
#include "..\Core\Logger.mqh"

// Bar-close shorthand ("closed bar") maps onto real MQL5 series indices
// (0 = still-forming bar) as shift 1 - this class always reads starting at
// shift 1, never shift 0, so results never repaint.
class CMeanRevSignal
  {
private:
   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf;
   int               m_hSMAShort;
   int               m_hSMALong;
   int               m_hRSI;
   double            m_rsiOverbought;
   double            m_rsiOversold;
   int               m_streakLength;
   CLogger          *m_logger;

   //--- counts consecutive in-zone closed bars starting at shift 1, walking
   //--- backward through rsi[] (index 0 = shift 1, oldest-to-newest reversed
   //--- via ArraySetAsSeries); stops at the first out-of-zone bar, which
   //--- reproduces the notebook's groupby(run_id).cumsum() streak exactly.
   int               CountStreak(const double &rsi[], const int n, const bool overboughtSide) const
     {
      int count = 0;
      for(int i = 0; i < n; i++)
        {
         const bool inZone = overboughtSide ? (rsi[i] > m_rsiOverbought) : (rsi[i] < m_rsiOversold);
         if(!inZone)
            break;
         count++;
         if(count > m_streakLength)
            break; // already proven != streakLength, no need to keep counting
        }
      return count;
     }

public:
                     CMeanRevSignal(void) : m_symbol(_Symbol), m_tf(PERIOD_CURRENT),
                                             m_hSMAShort(INVALID_HANDLE), m_hSMALong(INVALID_HANDLE), m_hRSI(INVALID_HANDLE),
                                             m_rsiOverbought(80.0), m_rsiOversold(20.0), m_streakLength(2), m_logger(NULL) {}

   bool              Init(const string symbol, const ENUM_TIMEFRAMES tf,
                           const int smaShortPeriod, const int smaLongPeriod, const int rsiPeriod,
                           const double rsiOverbought, const double rsiOversold, const int streakLength,
                           CLogger *logger)
     {
      m_symbol        = symbol;
      m_tf            = tf;
      m_rsiOverbought = rsiOverbought;
      m_rsiOversold   = rsiOversold;
      m_streakLength  = streakLength;
      m_logger        = logger;

      m_hSMAShort = iMA(symbol, tf, smaShortPeriod, 0, MODE_SMA, PRICE_CLOSE);
      m_hSMALong  = iMA(symbol, tf, smaLongPeriod,  0, MODE_SMA, PRICE_CLOSE);
      // MQL5's built-in RSI already uses Wilder smoothing internally, matching
      // the ewm(alpha=1/period, adjust=False) formula used in the research notebook.
      m_hRSI      = iRSI(symbol, tf, rsiPeriod, PRICE_CLOSE);

      if(m_hSMAShort == INVALID_HANDLE || m_hSMALong == INVALID_HANDLE || m_hRSI == INVALID_HANDLE)
        {
         if(m_logger != NULL)
            m_logger.Log(LOG_ERROR, "CMeanRevSignal::Init - failed to create SMA/RSI indicator handle(s)");
         return false;
        }
      return true;
     }

   void              Deinit(void)
     {
      if(m_hSMAShort != INVALID_HANDLE)
         IndicatorRelease(m_hSMAShort);
      if(m_hSMALong != INVALID_HANDLE)
         IndicatorRelease(m_hSMALong);
      if(m_hRSI != INVALID_HANDLE)
         IndicatorRelease(m_hRSI);
      m_hSMAShort = INVALID_HANDLE;
      m_hSMALong  = INVALID_HANDLE;
      m_hRSI      = INVALID_HANDLE;
     }

   //--- evaluates the bar that just closed (shift 1); non-repainting.
   //--- Fires exactly once per streak, on the bar where the consecutive
   //--- in-zone count first reaches m_streakLength (not "at least").
   bool              CheckForSignal(ENUM_TRIGGER_DIR &dir, datetime &signalBarTime)
     {
      dir = TRIGGER_NONE;

      const int n = m_streakLength + 1; // just enough to tell streak==X from streak>X
      double smaShort[], smaLong[], rsi[];
      ArraySetAsSeries(smaShort, true);
      ArraySetAsSeries(smaLong,  true);
      ArraySetAsSeries(rsi,      true);

      if(CopyBuffer(m_hSMAShort, 0, 1, 1, smaShort) != 1)
         return false;
      if(CopyBuffer(m_hSMALong, 0, 1, 1, smaLong) != 1)
         return false;
      if(CopyBuffer(m_hRSI, 0, 1, n, rsi) != n)
         return false; // not enough closed-bar history yet

      const bool uptrend = smaShort[0] > smaLong[0];

      const int oversoldStreak   = CountStreak(rsi, n, false);
      const int overboughtStreak = CountStreak(rsi, n, true);

      const bool buyReady  = uptrend  && (oversoldStreak   == m_streakLength);
      const bool sellReady = !uptrend && (overboughtStreak == m_streakLength);

      if(m_logger != NULL)
        {
         const datetime barTime    = iTime(m_symbol, m_tf, 1);
         const string   barTimeStr = TimeToString(barTime, TIME_DATE | TIME_MINUTES);
         m_logger.Log(LOG_DEBUG, StringFormat(
            "CheckForSignal[%s]: uptrend=%s smaShort=%.5f smaLong=%.5f rsi=%.2f oversoldStreak=%d overboughtStreak=%d buyReady=%s sellReady=%s",
            barTimeStr, uptrend ? "true" : "false", smaShort[0], smaLong[0], rsi[0],
            oversoldStreak, overboughtStreak, buyReady ? "true" : "false", sellReady ? "true" : "false"));
        }

      if(buyReady)
        {
         dir           = TRIGGER_BUY;
         signalBarTime = iTime(m_symbol, m_tf, 1);
         return true;
        }
      if(sellReady)
        {
         dir           = TRIGGER_SELL;
         signalBarTime = iTime(m_symbol, m_tf, 1);
         return true;
        }
      return false;
     }
  };
