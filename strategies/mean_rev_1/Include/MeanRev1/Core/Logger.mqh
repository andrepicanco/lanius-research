//+------------------------------------------------------------------+
//|                                                     Logger.mqh   |
//|                      MeanRev1 - leveled Print() wrapper          |
//+------------------------------------------------------------------+
#property strict

#include "..\Defines.mqh"

class CLogger
  {
private:
   ENUM_LOG_LEVEL    m_minLevel;

   string            LevelTag(const ENUM_LOG_LEVEL lvl) const
     {
      switch(lvl)
        {
         case LOG_DEBUG: return "DEBUG";
         case LOG_INFO:  return "INFO";
         case LOG_WARN:  return "WARN";
         case LOG_ERROR: return "ERROR";
        }
      return "?";
     }

public:
                     CLogger(void) : m_minLevel(LOG_INFO) {}

   void              Init(const ENUM_LOG_LEVEL minLevel) { m_minLevel = minLevel; }

   void              Log(const ENUM_LOG_LEVEL lvl, const string msg) const
     {
      if(lvl < m_minLevel)
         return;
      PrintFormat("[MeanRev1][%s][%s] %s", _Symbol, LevelTag(lvl), msg);
     }
  };
