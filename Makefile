IMG_NAMESPACE = asteven
IMG_NAME = zfs-provisioner
IMG_FQNAME = $(IMG_NAMESPACE)/$(IMG_NAME)
IMG_VERSION = 0.1.0

.PHONY: populate-cache build-cache build-runtime push clean
all: build-runtime

populate-cache:
	# Pull the latest version of the image, in order to
	# populate the build cache:
	sudo docker pull $(IMG_FQNAME):compile-stage || true
	sudo docker pull $(IMG_FQNAME):latest || true

build-cache: populate-cache
	# Build the compile stage:
	sudo docker build --target compile-image \
		--cache-from=$(IMG_FQNAME):compile-stage \
		--tag $(IMG_FQNAME):compile-stage .

build-runtime: build-cache
	# Build the runtime stage, using cached compile stage:
	sudo docker build --target runtime-image \
		--cache-from=$(IMG_FQNAME):compile-stage \
		--cache-from=$(IMG_FQNAME):latest \
		--tag $(IMG_FQNAME):$(IMG_VERSION) \
		--tag $(IMG_FQNAME):latest .

push:
	sudo docker push $(IMG_FQNAME):compile-stage
	sudo docker push $(IMG_FQNAME):$(IMG_VERSION)
	# Also update :latest
	sudo docker push $(IMG_FQNAME):latest

clean:
	sudo docker rmi $(IMG_FQNAME):$(IMG_VERSION)
	sudo docker rmi $(IMG_FQNAME):latest

