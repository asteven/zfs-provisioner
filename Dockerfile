# Dockerfile based on ideas from https://pythonspeed.com/

FROM python:3.8-alpine AS compile-image

LABEL maintainer "Steven Armstrong <steven.armstrong@id.ethz.ch>"

RUN apk --no-cache -X "@edge http://dl-cdn.alpinelinux.org/alpine/edge/main" \
   add --upgrade apk-tools@edge \
   build-base gcc \
   git

ENV VIRTUAL_ENV=/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install python packages into virtual env.
RUN mkdir /application
COPY . /application
RUN cd /application; pip install -r requirements.txt || true
RUN cd /application; pip install .

# Dumb-init as pid 1.
RUN pip install dumb-init


FROM python:3.8-alpine AS runtime-image

# Install runtime dependencies.
COPY install-packages.sh .
RUN ./install-packages.sh

COPY --from=compile-image /venv /venv

# Ensure exectutables from virtualenv are prefered.
ENV PATH="/venv/bin:$PATH"
ENTRYPOINT ["/venv/bin/dumb-init", "--", "zfs-provisioner"]
CMD ["--help"]

