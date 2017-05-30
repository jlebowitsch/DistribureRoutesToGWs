## RouteRedistibution for GWs

The purpose of this script is to allow, in AWS VPCs, the automatic reassignments of route-targets to healthy gateways, when these gateways are monitored by an ELB.



### Prerequisites and installation

- Have gateways that act as routers (like Check Point vSEC gateways) up and running.  
- Have routes tables and routes set up so that all the subnets in the same Availability zones are associated with a routing table and routes that point to the same gateway as the next hop (AKA, the target). Prefer to use gateway that's in an AZ serve the subnets of that AZ. make sure that in any one routing table, only one gateway is mentioned in any of the routes. 
- if the gateway has more than one ENI, make sure that the gateway knows to route to the subnets behind it through ethN where N is maximal. E.g., if the gateway has eth0 and eth1, make sure that each of the gateways knows that traffic to each of the protected subnets should be forwarded through eth1
- Set up an ELB to monitor the gateways. (the gateways can be part of an Autoscaling Group or not)
- Create an cloudwatch alarm that will send notification to some SNS topic if the number of unhealthy instances is larger than 0. Add to the alarm a notification, to the same topic, if the status changes back to OK
- Create a lambda function that uses the script here, and have it triggered by notifications to that SNS topic 
- Change the value of the "elbname" variable, in the begining of the script, to be the name of the loadbalancer that's monitoring your gateways


### What the script does

On execution, the script will 
1)  find all the route tables that have routes pointing to gateways that are not in service, 
2) for each such route table, it finds what's the AZ that's the most common among all the subnets that use this route table
3) for each such route table, to any of the routes that use any of the gateways, the script assign a new healthy gateway. If healthy gateway exist in the dominant AZ, it assigns the least used from among them. otherwise it assignes from the least used among all of them
4) then, the script finds any healthy gateways that are unused. If there are route tables dominated by the same AZ as that gateway that are assigned to GWs in different AZs, then it reassigns the relevant routes of this route table to this unused gateway

### Note to consider
currently Cloudwatch alerts must aggregate events at minimal time resultion of one minute. Thus this method of controlling routes will typically lag by one minute after a gateway first becomes "OutofService"

