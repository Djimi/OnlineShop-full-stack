package com.onlineshop.gateway.handler;

import com.onlineshop.gateway.dto.ProductInfoResponse;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.function.ServerRequest;
import org.springframework.web.servlet.function.ServerResponse;

import java.util.UUID;

@Component
public class ProductInfoHandler {

    private static final ProductInfoResponse RESPONSE =
            new ProductInfoResponse("This is a test product for learning");

    public ServerResponse getProductInfo(ServerRequest request) {
        return ServerResponse.ok()
                .contentType(MediaType.APPLICATION_JSON)
                .header("randomheader", UUID.randomUUID().toString())
                .body(RESPONSE);
    }
}
