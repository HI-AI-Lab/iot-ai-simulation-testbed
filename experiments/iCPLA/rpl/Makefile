CONTIKI ?= ../../../../contiki-ng

# Application
CONTIKI_PROJECT = app
PROJECT_SOURCEFILES += rpl-icpla.c

all: $(CONTIKI_PROJECT)

# Use COOJA
TARGET ?= cooja

# (Optional) make the α parameter easy to override from CLI:
#   make app.cooja TARGET=cooja ICPLA_ALPHA_FP=64
CFLAGS += -DICPLA_ALPHA_FP=$(ICPLA_ALPHA_FP)

# Normal Contiki-NG include
include $(CONTIKI)/Makefile.include
