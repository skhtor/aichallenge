package main

import (
	"bufio"
	"fmt"
	"math/rand"
	"os"
	"strconv"
	"strings"
)

const (
	WATER = -4
	FOOD  = -3
	LAND  = -2
	DEAD  = -1
)

type State struct {
	Rows, Cols    int
	Turns         int
	TurnTime      int
	LoadTime      int
	ViewRadius2   int
	AttackRadius2 int
	SpawnRadius2  int
	Grid          [][]int
}

func NewState() *State {
	return &State{}
}

func (s *State) Setup(params map[string]int) {
	s.Rows = params["rows"]
	s.Cols = params["cols"]
	s.Turns = params["turns"]
	s.TurnTime = params["turntime"]
	s.LoadTime = params["loadtime"]
	s.ViewRadius2 = params["viewradius2"]
	s.AttackRadius2 = params["attackradius2"]
	s.SpawnRadius2 = params["spawnradius2"]
	s.Grid = make([][]int, s.Rows)
	for r := range s.Grid {
		s.Grid[r] = make([]int, s.Cols)
		for c := range s.Grid[r] {
			s.Grid[r][c] = LAND
		}
	}
}

func (s *State) Reset() {
	for r := range s.Grid {
		for c := range s.Grid[r] {
			if s.Grid[r][c] != WATER {
				s.Grid[r][c] = LAND
			}
		}
	}
}

func (s *State) Passable(row, col int) bool {
	return s.Grid[row][col] != WATER
}

func (s *State) Destination(row, col int, dir byte) (int, int) {
	switch dir {
	case 'n':
		return (row - 1 + s.Rows) % s.Rows, col
	case 's':
		return (row + 1) % s.Rows, col
	case 'e':
		return row, (col + 1) % s.Cols
	case 'w':
		return row, (col - 1 + s.Cols) % s.Cols
	}
	return row, col
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	state := NewState()
	params := make(map[string]int)

	dirs := []byte{'n', 'e', 's', 'w'}
	var myAnts [][2]int

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		if line == "ready" {
			state.Setup(params)
			fmt.Println("go")
			continue
		}

		if line == "go" {
			// Do turn: move each ant in a random passable direction
			for _, ant := range myAnts {
				perm := rand.Perm(4)
				for _, i := range perm {
					d := dirs[i]
					nr, nc := state.Destination(ant[0], ant[1], d)
					if state.Passable(nr, nc) {
						fmt.Printf("o %d %d %c\n", ant[0], ant[1], d)
						break
					}
				}
			}
			fmt.Println("go")
			myAnts = myAnts[:0]
			state.Reset()
			continue
		}

		if line == "end" {
			break
		}

		parts := strings.Fields(line)
		if len(parts) >= 2 {
			switch parts[0] {
			case "turn":
				// new turn
			case "w":
				if len(parts) >= 3 {
					r, _ := strconv.Atoi(parts[1])
					c, _ := strconv.Atoi(parts[2])
					state.Grid[r][c] = WATER
				}
			case "f":
				if len(parts) >= 3 {
					r, _ := strconv.Atoi(parts[1])
					c, _ := strconv.Atoi(parts[2])
					state.Grid[r][c] = FOOD
				}
			case "a":
				if len(parts) >= 4 {
					r, _ := strconv.Atoi(parts[1])
					c, _ := strconv.Atoi(parts[2])
					owner, _ := strconv.Atoi(parts[3])
					state.Grid[r][c] = owner
					if owner == 0 {
						myAnts = append(myAnts, [2]int{r, c})
					}
				}
			case "h":
				// hill
			case "d":
				if len(parts) >= 3 {
					r, _ := strconv.Atoi(parts[1])
					c, _ := strconv.Atoi(parts[2])
					state.Grid[r][c] = DEAD
				}
			default:
				// setup parameter
				val, err := strconv.Atoi(parts[1])
				if err == nil {
					params[parts[0]] = val
				}
			}
		}
	}
}
