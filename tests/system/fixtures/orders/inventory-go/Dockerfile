FROM golang:1.22 AS build
WORKDIR /src
COPY . .
RUN go build -o /inventory .

FROM gcr.io/distroless/base-debian12
COPY --from=build /inventory /inventory
EXPOSE 8080
ENTRYPOINT ["/inventory"]
