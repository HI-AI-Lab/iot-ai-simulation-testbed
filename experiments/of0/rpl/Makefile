CONTIKI_PROJECT = app
TARGET = cooja

CFLAGS += -ULOG_WITH_COMPACT_ADDR -DLOG_WITH_COMPACT_ADDR=0

CONTIKI = /workspace/contiki-ng

# 🔥 Force RPL Classic sources into the build
MODULES_REL += os/net/routing/rpl-classic

# 🔥 Optional: force rebuild
MAKE_CLEAN = 1

include $(CONTIKI)/Makefile.include
