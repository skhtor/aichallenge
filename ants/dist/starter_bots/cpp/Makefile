CC=g++
CFLAGS=-O2 -std=c++17 -c
LDFLAGS=-O2 -lm
SOURCES=Bot.cc MyBot.cc State.cc
OBJECTS=$(addsuffix .o, $(basename ${SOURCES}))
EXECUTABLE=MyBot

#Uncomment the following to enable debugging
#CFLAGS+=-g -DDEBUG

all: $(OBJECTS) $(EXECUTABLE)

$(EXECUTABLE): $(OBJECTS)
	$(CC) $(LDFLAGS) $(OBJECTS) -o $@

.cc.o: *.h
	$(CC) $(CFLAGS) $< -o $@

clean: 
	-rm -f ${EXECUTABLE} ${OBJECTS} *.d
	-rm -f debug.txt

.PHONY: all clean

