CONTIKI_PROJECT = app
TARGET = cooja

CONTIKI = /workspace/contiki-ng

# Force inclusion of OF0
MODULES += os/net/routing/rpl-lite/rpl-of0

# -------------------------------
# Choose packet rate (comment/uncomment as needed)
# Default: 80 PPM → 0.75 s
CFLAGS += -DSEND_INTERVAL_SEC=0.75

# 100 PPM → 0.6 s
# CFLAGS += -DSEND_INTERVAL_SEC=0.6

# 120 PPM → 0.5 s
# CFLAGS += -DSEND_INTERVAL_SEC=0.5
# -------------------------------

include $(CONTIKI)/Makefile.include
