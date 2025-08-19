CONTIKI ?= ../../../../contiki-ng

CONTIKI_PROJECT = app
TARGET ?= cooja

all: $(CONTIKI_PROJECT)

MODULES += $(CONTIKI_NG_SERVICES_DIR)/shell

CFLAGS += -DPROJECT_CONF_H=\"project-conf.h\"

include $(CONTIKI)/Makefile.include
