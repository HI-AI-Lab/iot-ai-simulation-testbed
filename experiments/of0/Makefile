CONTIKI_PROJECT = app
TARGET = cooja
CONTIKI = /workspace/contiki-ng

# --- Force RPL-Lite + OF0 globally ---
CFLAGS  += -DNETSTACK_CONF_ROUTING=rpl_lite_driver \
           -DRPL_CONF_OF=rpl_of0 \
           -DRPL_CONF_OF_OCP=RPL_OCP_OF0 \
           -DRPL_CONF_DEFAULT_INSTANCE=0 \
           -DRPL_CONF_WITH_DAO=1 \
           -DUIP_CONF_ROUTER=1

MODULES += os/net/routing/rpl-lite/rpl-of0

# --- Packet rate (choose ONE, comment the others) ---
CFLAGS += -DSEND_INTERVAL_SEC=0.75   # 80 PPM (default)
# CFLAGS += -DSEND_INTERVAL_SEC=0.6  # 100 PPM
# CFLAGS += -DSEND_INTERVAL_SEC=0.5  # 120 PPM

include $(CONTIKI)/Makefile.include