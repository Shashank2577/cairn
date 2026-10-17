package com.example.checkout;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class StockEvents {
    @KafkaListener(topics = "stock.reserved", groupId = "checkout")
    public void onReserved(String payload) {
        System.out.println("reserved " + payload);
    }
}
