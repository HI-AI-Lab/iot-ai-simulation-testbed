CONTIKI ?= ../../../../contiki-ng

CONTIKI_PROJECT = app
PROJECT_SOURCEFILES += rpl-icpla.c

CFLAGS += -DPROJECT_CONF_H=\"project-conf.h\"
CFLAGS += -DICPLA_ENABLE_RL=1
# keep compiles strict but avoid false-positive gate on indentation in legacy code
CFLAGS += -Wno-error=misleading-indentation

all: $(CONTIKI_PROJECT).cooja
include $(CONTIKI)/Makefile.include
