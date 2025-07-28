CONTIKI_PROJECT = app
TARGET = cooja

CFLAGS += -ULOG_WITH_COMPACT_ADDR -DLOG_WITH_COMPACT_ADDR=1

CONTIKI = /workspace/contiki-ng

# 👇 This is the key line to fix your error
MODULES_REL += os/net/routing/rpl-classic

include $(CONTIKI)/Makefile.include
