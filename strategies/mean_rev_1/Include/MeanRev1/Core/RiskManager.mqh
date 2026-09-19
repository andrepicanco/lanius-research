//+------------------------------------------------------------------+
//|                                                RiskManager.mqh   |
//|           MeanRev1 - instrument-agnostic lot size calculation    |
//+------------------------------------------------------------------+
#property strict

#include "Logger.mqh"

class CRiskManager
  {
private:
   string            m_symbol;
   bool              m_useFixedLot;
   double            m_fixedLot;
   double            m_riskPercent;
   CLogger          *m_logger;

   double            NormalizeVolume(double volume) const
     {
      const double stepVol = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
      const double minVol  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
      const double maxVol  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);

      if(stepVol > 0.0)
         volume = MathFloor(volume / stepVol) * stepVol;

      if(volume < minVol)
         volume = minVol;
      if(volume > maxVol)
         volume = maxVol;

      return volume;
     }

public:
                     CRiskManager(void) : m_symbol(_Symbol),
                                           m_useFixedLot(false),
                                           m_fixedLot(0.10),
                                           m_riskPercent(1.0),
                                           m_logger(NULL) {}

   void              Init(const string symbol, const bool useFixedLot, const double fixedLot,
                           const double riskPercent, CLogger *logger)
     {
      m_symbol      = symbol;
      m_useFixedLot = useFixedLot;
      m_fixedLot    = fixedLot;
      m_riskPercent = riskPercent;
      m_logger      = logger;
     }

   //--- slDistance is the SL distance in price units (points*point), not pips
   double            CalculateLotSize(const double slDistance)
     {
      if(m_useFixedLot)
         return NormalizeVolume(m_fixedLot);

      if(slDistance <= 0.0)
        {
         if(m_logger != NULL)
            m_logger.Log(LOG_ERROR, "CalculateLotSize: invalid SL distance <= 0, falling back to volume min");
         return SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
        }

      const double tickValue = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE);
      const double tickSize  = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);

      if(tickValue <= 0.0 || tickSize <= 0.0)
        {
         if(m_logger != NULL)
            m_logger.Log(LOG_ERROR, "CalculateLotSize: broker returned invalid tick value/size, falling back to volume min");
         return SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
        }

      const double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
      const double riskMoney  = balance * (m_riskPercent / 100.0);
      const double valuePerUnit = (slDistance / tickSize) * tickValue; // loss per 1.0 lot if SL hit
      if(valuePerUnit <= 0.0)
         return SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);

      const double rawLots = riskMoney / valuePerUnit;
      return NormalizeVolume(rawLots);
     }
  };
