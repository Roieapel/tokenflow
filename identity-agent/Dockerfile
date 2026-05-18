FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -o identity-agent .

FROM alpine:3.19
WORKDIR /app
COPY --from=builder /app/identity-agent .
EXPOSE 8081
CMD ["./identity-agent"]
