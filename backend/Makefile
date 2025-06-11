SERVER ?= 192.168.20.145

.PHONY: build deploy run install venv

build:
	docker build -t localhost/newfront .

deploy: build
	docker save localhost/newfront > /tmp/newfront.tar
	scp -r /tmp/newfront.tar root@$(SERVER):/tmp
	make deploy-auto
	rm /tmp/newfront.tar

deploy-auto:
	@if ssh root@$(SERVER) '[ -f /etc/redhat-release ] || [ -f /etc/rocky-release ] || [ -f /etc/almalinux-release ] || [ -f /etc/centos-release ]'; then \
		echo "Distribution RHEL-based détectée, utilisation de podman"; \
		make deploy-podman; \
	elif ssh root@$(SERVER) '[ -f /etc/debian_version ]'; then \
		echo "Distribution Debian-based détectée, utilisation de docker"; \
		make deploy-docker; \
	else \
		echo "Distribution non reconnue!"; \
	fi

deploy-podman:
	ssh root@$(SERVER) 'which podman >/dev/null 2>&1 || dnf install -y podman 2>/dev/null || yum install -y podman'
	scp acic-database.service root@$(SERVER):/etc/systemd/system
	ssh root@$(SERVER) 'systemctl daemon-reload'
	ssh root@$(SERVER) 'systemctl enable acic-database.service'
	ssh root@$(SERVER) 'systemctl stop acic-database.service'
	ssh root@$(SERVER) 'mkdir -p /opt/ACIC/front/database'
	ssh root@$(SERVER) 'podman load < /tmp/newfront.tar'
	ssh root@$(SERVER) 'rm /tmp/newfront.tar'
	ssh root@$(SERVER) 'systemctl start acic-database.service'
	ssh root@$(SERVER) 'podman system prune -f'

deploy-docker: build
	ssh root@$(SERVER) 'which docker >/dev/null 2>&1 || apt install -y docker 2>/dev/null || apt-get install -y docker'
	ssh root@$(SERVER) 'docker stop $$(docker ps -q --filter ancestor=localhost/newfront) 2>/dev/null || true'
	ssh root@$(SERVER) 'docker load < /tmp/newfront.tar'
	ssh root@$(SERVER) 'rm /tmp/newfront.tar'
	ssh root@$(SERVER) 'mkdir -p /opt/ACIC/front/database'
	ssh root@$(SERVER) 'docker run -d --restart unless-stopped --shm-size=512m --net host -v /opt/ACIC/front/database:/var/lib/postgresql/16/main:z localhost/newfront'
	ssh root@$(SERVER) 'docker system prune -f'

deploy-detect-distro:
	@echo "Détection du type de distribution sur $(SERVER)..."
	@if ssh root@$(SERVER) '[ -f /etc/redhat-release ] || [ -f /etc/rocky-release ] || [ -f /etc/almalinux-release ] || [ -f /etc/centos-release ]'; then \
		echo "✓ Distribution RHEL-based détectée (basée sur podman)"; \
		ssh root@$(SERVER) 'cat /etc/redhat-release 2>/dev/null || cat /etc/rocky-release 2>/dev/null || cat /etc/almalinux-release 2>/dev/null || cat /etc/centos-release 2>/dev/null'; \
	elif ssh root@$(SERVER) '[ -f /etc/debian_version ]'; then \
		echo "✓ Distribution Debian-based détectée (basée sur docker)"; \
		ssh root@$(SERVER) 'cat /etc/debian_version'; \
	else \
		echo "⚠ Distribution non reconnue!"; \
	fi

dist: build
	mkdir -p dist
	docker save localhost/newfront > dist/newfront.tar
	cp acic-database.service dist/

run:
	make venv CMD="python src/main.py --port 5021"	

install:
	sudo apt install python3-venv subversion
	python3 -m venv .venv
	make venv CMD="pip install -r requirements.txt"	

clean:
	rm -rf .venv/ dist/

venv:
	bash -c 'source .venv/bin/activate ; $(CMD)'

