FROM golang:1.22
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . ./
RUN go build -o /out/notify .
CMD ["/out/notify"]
