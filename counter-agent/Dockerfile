FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -o counter-agent .

FROM alpine:3.19
WORKDIR /app
COPY --from=builder /app/counter-agent .
EXPOSE 8082
CMD ["./counter-agent"]
