FROM debian:13.8-slim
RUN apt-get update && apt-get install -y --no-install-recommends ttyd openssh-client ca-certificates && apt-get clean && find /var/lib/apt/lists -type f -delete
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin terminal
USER 1000:1000
EXPOSE 7681
ENTRYPOINT []
