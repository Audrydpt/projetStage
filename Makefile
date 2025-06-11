export SERVER := 192.168.20.145

.PHONY: build install

build:
	$(MAKE) -C frontend build
	$(MAKE) -C backend build

deploy:
	$(MAKE) -C frontend deploy
	$(MAKE) -C backend deploy

install:
	$(MAKE) -C frontend install
	$(MAKE) -C backend install

dist:
	$(MAKE) -C frontend dist
	$(MAKE) -C backend dist

	rm -Rf dist/frontend/ dist/backend/
	mkdir -p dist/frontend/ dist/backend/

	mv frontend/dist/* dist/frontend/
	mv backend/dist/* dist/backend/

clean:
	rm -Rf dist/
	$(MAKE) -C frontend clean
	$(MAKE) -C backend clean