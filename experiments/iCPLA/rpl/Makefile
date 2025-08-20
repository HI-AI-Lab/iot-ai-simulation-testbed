# experiments/iCPLA/rpl/Makefile

CONTIKI ?= ../../../../contiki-ng

CONTIKI_PROJECT = app
TARGET ?= cooja
all: $(CONTIKI_PROJECT)

# Build RPL-Lite and link our custom OF
MODULES += os/net/routing/rpl-lite
CONTIKI_PROJECT_SOURCEFILES += rpl-icpla.c

# Use our project configuration
CFLAGS += -DPROJECT_CONF_H=\"project-conf.h\"

include $(CONTIKI)/Makefile.include
