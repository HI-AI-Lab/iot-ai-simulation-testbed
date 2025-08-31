CONTIKI ?= /workspace/contiki-ng
TARGET  ?= cooja

PROJECT_SOURCEFILES += app.c

# Be strict on our app only; do not force -Werror on Contiki core.
CFLAGS           += -Wall -Wextra
CFLAGS_app.o     += -Werror -Wno-error=implicit-fallthrough

# Build the default app image for Cooja
all: app.cooja

include $(CONTIKI)/Makefile.include
