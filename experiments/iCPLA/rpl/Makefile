CONTIKI ?= ../../../../contiki-ng

CONTIKI_PROJECT = app
PROJECT_SOURCEFILES += rpl-icpla.c

CFLAGS += -DPROJECT_CONF_H=\"project-conf.h\"
CFLAGS += -DICPLA_ENABLE_RL=1

all: $(CONTIKI_PROJECT).cooja
include $(CONTIKI)/Makefile.include
