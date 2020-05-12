REGISTRY = docker.io
IMG_NAMESPACE = asteven
IMG_NAME = zfs-provisioner
#IMG_FQNAME = $(IMG_NAMESPACE)/$(IMG_NAME)
IMG_FQNAME = $(REGISTRY)/$(IMG_NAMESPACE)/$(IMG_NAME)
IMG_VERSION = 0.1.0
#BUILDER = $(shell which docker)
BUILDER = $(shell which podman)

.PHONY: populate-cache build-cache build-runtime push clean
all: build-runtime

populate-cache:
	# Pull the latest version of the image, in order to
	# populate the build cache:
	sudo $(BUILDER) pull $(IMG_FQNAME):compile-stage || true
	sudo $(BUILDER) pull $(IMG_FQNAME):latest || true

build-cache: populate-cache
	# Build the compile stage:
	sudo $(BUILDER) build --target compile-image \
		--cache-from=$(IMG_FQNAME):compile-stage \
		--tag $(IMG_FQNAME):compile-stage .

build-runtime: build-cache
	# Build the runtime stage, using cached compile stage:
	sudo $(BUILDER) build --target runtime-image \
		--cache-from=$(IMG_FQNAME):compile-stage \
		--cache-from=$(IMG_FQNAME):latest \
		--tag $(IMG_FQNAME):$(IMG_VERSION) \
		--tag $(IMG_FQNAME):latest .

push:
	sudo $(BUILDER) push $(IMG_FQNAME):compile-stage docker://$(IMG_FQNAME):compile-stage
	sudo $(BUILDER) push $(IMG_FQNAME):$(IMG_VERSION) docker://$(IMG_FQNAME):$(IMG_VERSION)
	# Also update :latest
	sudo $(BUILDER) push $(IMG_FQNAME):latest docker://$(IMG_FQNAME):latest

clean:
	sudo $(BUILDER) rmi $(IMG_FQNAME):$(IMG_VERSION)
	sudo $(BUILDER) rmi $(IMG_FQNAME):latest

mrproper: clean
	sudo $(BUILDER) rmi $(IMG_FQNAME):compile-stage
