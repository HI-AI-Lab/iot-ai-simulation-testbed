CONTIKI_PROJECT = app
TARGET = cooja

CFLAGS += -ULOG_WITH_COMPACT_ADDR -DLOG_WITH_COMPACT_ADDR=0

CONTIKI = /workspace/contiki-ng

# Include the RPL Classic module explicitly
MODULES_REL += os/net/routing/rpl-classic
PROJECTDIRS += $(CONTIKI)/os/net/routing/rpl-classic

include $(CONTIKI)/Makefile.include
