FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -o pool-agent .

FROM alpine:3.19
WORKDIR /app
COPY --from=builder /app/pool-agent .
EXPOSE 8083
CMD ["./pool-agent"]
