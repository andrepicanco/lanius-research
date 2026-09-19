//+------------------------------------------------------------------+
//|                                                 BarManager.mqh   |
//|                MeanRev1 - non-repainting new-bar detection       |
//+------------------------------------------------------------------+
#property strict

class CBarManager
  {
private:
   datetime          m_lastBarTime;

public:
                     CBarManager(void) : m_lastBarTime(0) {}

   void              Reset(void) { m_lastBarTime = 0; }

   //--- returns true exactly once per new closed bar (shift-0 open time changed)
   bool              IsNewBar(const string symbol, const ENUM_TIMEFRAMES tf)
     {
      const datetime currentBarTime = iTime(symbol, tf, 0);
      if(currentBarTime == 0)
         return false; // not enough history / handle not ready yet

      if(currentBarTime != m_lastBarTime)
        {
         m_lastBarTime = currentBarTime;
         return true;
        }
      return false;
     }
  };
