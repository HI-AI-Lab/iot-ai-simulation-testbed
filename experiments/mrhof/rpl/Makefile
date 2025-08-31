CONTIKI_PROJECT = app
CONTIKI_NG ?= ../../../../contiki-ng

CFLAGS += -DPROJECT_CONF_H=\"project-conf.h\"
CFLAGS += -Wall -Wextra -Werror \
          -Wno-error=unused-parameter \
          -Wno-error=implicit-fallthrough \
          -Wno-error=sign-compare

include $(CONTIKI_NG)/Makefile.include
