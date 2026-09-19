//+------------------------------------------------------------------+
//|                                                    Defines.mqh   |
//|                            MeanRev1 - enums and shared constants |
//+------------------------------------------------------------------+
#property strict

#define MEANREV1_VERSION "1.00"

enum ENUM_TRIGGER_DIR
  {
   TRIGGER_NONE = 0,
   TRIGGER_BUY  = 1,
   TRIGGER_SELL = 2
  };

// No STATE_PENDING here - MeanRev1 always enters at market on the same tick
// the signal is confirmed, so there's no pending-order state to represent.
enum ENUM_EA_STATE
  {
   STATE_IDLE = 0,
   STATE_IN_POSITION
  };

enum ENUM_LOG_LEVEL
  {
   LOG_DEBUG = 0,
   LOG_INFO  = 1,
   LOG_WARN  = 2,
   LOG_ERROR = 3
  };
